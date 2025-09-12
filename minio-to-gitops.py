#!/usr/bin/env python3
"""
Minio to GitOps Auto-Generator

Automatically scans Minio bucket and generates complete GitOps structure
with namespace-based organization, ArgoCD Applications, and Kustomizations.
"""

import os
import sys
import json
import yaml
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass
from minio import Minio
from minio.error import S3Error

@dataclass
class ClusterMapping:
    dev: str
    test: str
    preprod: str
    prod: str

@dataclass
class NamespaceConfig:
    name: str
    resources: Dict[str, List[str]]  # resource_type -> [file_names]
    cluster_mapping: ClusterMapping

class MinioGitOpsGenerator:
    def __init__(self, minio_config: dict, cluster_mappings: dict, git_repo: str):
        self.minio_client = Minio(
            minio_config['endpoint'],
            access_key=minio_config['access_key'],
            secret_key=minio_config['secret_key'],
            secure=minio_config.get('secure', False)
        )
        self.bucket_name = minio_config['bucket']
        self.bucket_prefix = minio_config.get('prefix', '')
        self.git_repo = git_repo
        self.cluster_mappings = cluster_mappings
        self.namespaces: List[NamespaceConfig] = []
    
    def scan_minio_bucket(self) -> List[NamespaceConfig]:
        """Scan Minio bucket and detect namespaces with their resources"""
        print(f"🔍 Scanning Minio bucket: {self.bucket_name}/{self.bucket_prefix}")
        
        namespace_resources = {}
        
        try:
            # List all objects in bucket
            objects = self.minio_client.list_objects(
                self.bucket_name, 
                prefix=self.bucket_prefix,
                recursive=True
            )
            
            for obj in objects:
                if not obj.object_name.endswith('.yaml'):
                    continue
                    
                # Parse path: prefix/namespace/resource-file.yaml
                path_parts = obj.object_name.replace(self.bucket_prefix, '').strip('/').split('/')
                
                if len(path_parts) >= 2:
                    namespace = path_parts[-2]  # Second to last part is namespace
                    filename = path_parts[-1]
                    
                    # Initialize namespace if not exists
                    if namespace not in namespace_resources:
                        namespace_resources[namespace] = {}
                    
                    # Categorize resource by filename pattern
                    resource_type = self._categorize_resource(filename)
                    if resource_type not in namespace_resources[namespace]:
                        namespace_resources[namespace][resource_type] = []
                    
                    namespace_resources[namespace][resource_type].append(filename)
                    
                    print(f"📄 Found: {namespace}/{resource_type}/{filename}")
        
        except S3Error as e:
            print(f"❌ Minio error: {e}")
            sys.exit(1)
        
        # Convert to NamespaceConfig objects
        for ns_name, resources in namespace_resources.items():
            # Use default cluster mapping or namespace-specific if provided
            cluster_mapping = ClusterMapping(
                dev=self.cluster_mappings.get(ns_name, {}).get('dev', self.cluster_mappings['default']['dev']),
                test=self.cluster_mappings.get(ns_name, {}).get('test', self.cluster_mappings['default']['test']),
                preprod=self.cluster_mappings.get(ns_name, {}).get('preprod', self.cluster_mappings['default']['preprod']),
                prod=self.cluster_mappings.get(ns_name, {}).get('prod', self.cluster_mappings['default']['prod'])
            )
            
            self.namespaces.append(NamespaceConfig(
                name=ns_name,
                resources=resources,
                cluster_mapping=cluster_mapping
            ))
        
        print(f"✅ Detected {len(self.namespaces)} namespaces: {[ns.name for ns in self.namespaces]}")
        return self.namespaces
    
    def _categorize_resource(self, filename: str) -> str:
        """Categorize Kubernetes resource by filename"""
        filename_lower = filename.lower()
        
        if any(word in filename_lower for word in ['deploy', 'deployment']):
            return 'deployments'
        elif any(word in filename_lower for word in ['service', 'svc']):
            return 'services'
        elif any(word in filename_lower for word in ['config', 'cm']):
            return 'configmaps'
        elif any(word in filename_lower for word in ['secret']):
            return 'secrets'
        elif any(word in filename_lower for word in ['pvc', 'persistent']):
            return 'persistentvolumeclaims'
        elif any(word in filename_lower for word in ['route']):
            return 'routes'
        elif any(word in filename_lower for word in ['ingress']):
            return 'ingress'
        elif any(word in filename_lower for word in ['cron', 'job']):
            return 'cronjobs'
        elif any(word in filename_lower for word in ['hpa', 'autoscal']):
            return 'hpa'
        elif any(word in filename_lower for word in ['image', 'stream']):
            return 'imagestreams'
        elif any(word in filename_lower for word in ['network', 'policy']):
            return 'networkpolicies'
        else:
            return 'other'
    
    def download_resources(self) -> None:
        """Download all resources from Minio to local filesystem"""
        print("📥 Downloading resources from Minio...")
        
        for namespace in self.namespaces:
            base_path = Path(f"namespaces/{namespace.name}/environments/dev")
            
            for resource_type, filenames in namespace.resources.items():
                resource_dir = base_path / resource_type
                resource_dir.mkdir(parents=True, exist_ok=True)
                
                for filename in filenames:
                    # Construct Minio object path
                    minio_path = f"{self.bucket_prefix}/{namespace.name}/{filename}".strip('/')
                    local_path = resource_dir / filename
                    
                    try:
                        # Download file from Minio
                        self.minio_client.fget_object(
                            self.bucket_name,
                            minio_path,
                            str(local_path)
                        )
                        
                        # Clean up Kubernetes metadata (remove UIDs, resourceVersion, etc.)
                        self._cleanup_k8s_metadata(local_path)
                        
                        print(f"📄 Downloaded: {minio_path} → {local_path}")
                        
                    except S3Error as e:
                        print(f"❌ Failed to download {minio_path}: {e}")
    
    def _cleanup_k8s_metadata(self, file_path: Path) -> None:
        """Remove Kubernetes-generated metadata from YAML files using advanced cleaner"""
        try:
            # Import the advanced cleaner (assuming it's in same directory)
            from advanced_yaml_cleanup import KubernetesYAMLCleaner
            
            # Use advanced cleaner
            cleaner = KubernetesYAMLCleaner()
            success = cleaner.clean_yaml_file(file_path)
            
            if success:
                print(f"🧹 Advanced cleanup completed: {file_path.name}")
            else:
                print(f"⚠️  Advanced cleanup failed: {file_path.name}")
                
        except ImportError:
            # Fallback to simple cleanup if advanced cleaner not available
            print(f"⚠️  Using fallback cleanup for {file_path.name}")
            self._simple_cleanup_k8s_metadata(file_path)
        except Exception as e:
            print(f"⚠️  Cleanup error for {file_path}: {e}")
            
    def _simple_cleanup_k8s_metadata(self, file_path: Path) -> None:
        """Fallback simple cleanup method"""
        try:
            with open(file_path, 'r') as f:
                docs = list(yaml.safe_load_all(f))
            
            cleaned_docs = []
            for doc in docs:
                if not doc:
                    continue
                
                # Remove metadata fields that shouldn't be in GitOps
                if 'metadata' in doc:
                    metadata_to_remove = [
                        'uid', 'resourceVersion', 'generation', 'creationTimestamp',
                        'managedFields', 'selfLink', 'finalizers', 'ownerReferences'
                    ]
                    for field in metadata_to_remove:
                        doc['metadata'].pop(field, None)
                
                # Remove status section (COMPREHENSIVE)
                doc.pop('status', None)
                
                # Resource-specific cleanup
                kind = doc.get('kind', '')
                if kind == 'Service' and 'spec' in doc:
                    doc['spec'].pop('clusterIP', None)
                    doc['spec'].pop('clusterIPs', None)
                elif kind == 'PersistentVolumeClaim' and 'spec' in doc:
                    doc['spec'].pop('volumeName', None)
                
                cleaned_docs.append(doc)
            
            with open(file_path, 'w') as f:
                yaml.dump_all(cleaned_docs, f, default_flow_style=False, sort_keys=False)
                
        except Exception as e:
            print(f"⚠️  Warning: Could not clean metadata from {file_path}: {e}")
    
    def generate_gitops_structure(self) -> None:
        """Generate complete GitOps structure for all namespaces"""
        print("🏗️  Generating GitOps structure...")
        
        for namespace in self.namespaces:
            self._generate_namespace_structure(namespace)
        
        # Generate root README
        self._generate_root_readme()
        
        print("✅ GitOps structure generation complete!")
    
    def _generate_namespace_structure(self, namespace: NamespaceConfig) -> None:
        """Generate complete structure for a single namespace"""
        print(f"📦 Generating structure for namespace: {namespace.name}")
        
        # Create directory structure
        ns_path = Path(f"namespaces/{namespace.name}")
        (ns_path / "argocd-apps").mkdir(parents=True, exist_ok=True)
        
        for env in ['dev', 'test', 'preprod', 'prod']:
            (ns_path / "environments" / env).mkdir(parents=True, exist_ok=True)
        
        # Generate ArgoCD Applications
        self._generate_argocd_apps(namespace)
        
        # Generate Kustomizations
        self._generate_kustomizations(namespace)
        
        # Generate namespace README
        self._generate_namespace_readme(namespace)
    
    def _generate_argocd_apps(self, namespace: NamespaceConfig) -> None:
        """Generate ArgoCD Application manifests for all environments"""
        apps_path = Path(f"namespaces/{namespace.name}/argocd-apps")
        
        environments = {
            'dev': {
                'cluster': namespace.cluster_mapping.dev,
                'target_namespace': f"{namespace.name}-dev",
                'sync_policy': {'automated': {'prune': True, 'selfHeal': True}}
            },
            'test': {
                'cluster': namespace.cluster_mapping.test,
                'target_namespace': namespace.name,
                'sync_policy': {'automated': {'prune': True, 'selfHeal': True}}
            },
            'preprod': {
                'cluster': namespace.cluster_mapping.preprod,
                'target_namespace': f"{namespace.name}-preprod",
                'sync_policy': {'automated': {'prune': False, 'selfHeal': False}}
            },
            'prod': {
                'cluster': namespace.cluster_mapping.prod,
                'target_namespace': namespace.name,
                'sync_policy': {'automated': {'prune': False, 'selfHeal': False}}
            }
        }
        
        for env, config in environments.items():
            app_manifest = {
                'apiVersion': 'argoproj.io/v1alpha1',
                'kind': 'Application',
                'metadata': {
                    'name': f"{namespace.name}-{env}",
                    'namespace': 'argocd',
                    'labels': {
                        'namespace': namespace.name,
                        'environment': env
                    }
                },
                'spec': {
                    'project': 'default',
                    'source': {
                        'repoURL': self.git_repo,
                        'targetRevision': 'main',
                        'path': f"namespaces/{namespace.name}/environments/{env}"
                    },
                    'destination': {
                        'server': config['cluster'],
                        'namespace': config['target_namespace']
                    },
                    'syncPolicy': {
                        **config['sync_policy'],
                        'syncOptions': ['CreateNamespace=true']
                    },
                    'info': [
                        {'name': 'Environment', 'value': env.title()},
                        {'name': 'Target Cluster', 'value': config['cluster']},
                        {'name': 'Namespace', 'value': config['target_namespace']}
                    ]
                }
            }
            
            app_file = apps_path / f"{env}.yaml"
            with open(app_file, 'w') as f:
                yaml.dump(app_manifest, f, default_flow_style=False, sort_keys=False)
            
            print(f"📄 Generated ArgoCD App: {app_file}")
    
    def _generate_kustomizations(self, namespace: NamespaceConfig) -> None:
        """Generate Kustomization files for all environments"""
        
        # Base kustomization (dev environment)
        dev_kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'resources': [f"{rt}/" for rt in namespace.resources.keys()],
            'namespace': f"{namespace.name}-dev",
            'namePrefix': 'dev-',
            'commonLabels': {
                'environment': 'dev',
                'app.kubernetes.io/managed-by': 'argocd',
                'app.kubernetes.io/part-of': namespace.name
            }
        }
        
        dev_file = Path(f"namespaces/{namespace.name}/environments/dev/kustomization.yaml")
        with open(dev_file, 'w') as f:
            yaml.dump(dev_kustomization, f, default_flow_style=False, sort_keys=False)
        
        # Other environments (overlays)
        overlays = {
            'test': {
                'namespace': namespace.name,
                'namePrefix': 'test-',
                'replicas': 1,
                'storage_patches': {'postgres-pvc': '1Gi', 'web-logs-pvc': '500Mi'}
            },
            'preprod': {
                'namespace': f"{namespace.name}-preprod", 
                'namePrefix': 'preprod-',
                'replicas': 2,
                'storage_patches': {'postgres-pvc': '10Gi', 'web-logs-pvc': '5Gi'}
            },
            'prod': {
                'namespace': namespace.name,
                'namePrefix': '',
                'replicas': 3,
                'storage_patches': {'postgres-pvc': '50Gi', 'web-logs-pvc': '20Gi'}
            }
        }
        
        for env, config in overlays.items():
            overlay_kustomization = {
                'apiVersion': 'kustomize.config.k8s.io/v1beta1',
                'kind': 'Kustomization',
                'resources': [f"../dev/{rt}/" for rt in namespace.resources.keys()],
                'namespace': config['namespace'],
                'commonLabels': {
                    'environment': env,
                    'app.kubernetes.io/managed-by': 'argocd',
                    'app.kubernetes.io/part-of': namespace.name
                }
            }
            
            if config['namePrefix']:
                overlay_kustomization['namePrefix'] = config['namePrefix']
            
            # Add patches for replicas and storage
            patches = []
            
            # Replica patches
            if 'deployments' in namespace.resources:
                patches.append({
                    'target': {'kind': 'Deployment', 'name': '.*'},
                    'patch': f'''- op: replace
  path: /spec/replicas
  value: {config['replicas']}
- op: add
  path: /spec/template/spec/containers/0/env/-
  value:
    name: ENVIRONMENT
    value: "{env}"'''
                })
            
            # Storage patches
            if 'persistentvolumeclaims' in namespace.resources:
                for pvc_name, size in config['storage_patches'].items():
                    patches.append({
                        'target': {'kind': 'PersistentVolumeClaim', 'name': pvc_name},
                        'patch': f'''- op: replace
  path: /spec/resources/requests/storage
  value: "{size}"'''
                    })
            
            if patches:
                overlay_kustomization['patches'] = patches
            
            overlay_file = Path(f"namespaces/{namespace.name}/environments/{env}/kustomization.yaml")
            with open(overlay_file, 'w') as f:
                yaml.dump(overlay_kustomization, f, default_flow_style=False, sort_keys=False)
            
            print(f"📄 Generated Kustomization: {overlay_file}")
    
    def _generate_namespace_readme(self, namespace: NamespaceConfig) -> None:
        """Generate README for namespace with deployment instructions"""
        
        # Count resources
        total_resources = sum(len(files) for files in namespace.resources.values())
        resource_summary = ", ".join([f"{len(files)} {rt}" for rt, files in namespace.resources.items()])
        
        readme_content = f"""# {namespace.name} Namespace

Auto-generated from Minio bucket. This namespace contains {total_resources} resources.

## 🎯 Resources
{resource_summary}

## 🌐 Environment → Cluster Mapping

| Environment | Target Cluster | Namespace | Sync Policy |
|------------|----------------|-----------|-------------|
| **dev** | `{namespace.cluster_mapping.dev}` | `{namespace.name}-dev` | Auto |
| **test** | `{namespace.cluster_mapping.test}` | `{namespace.name}` | Auto |
| **preprod** | `{namespace.cluster_mapping.preprod}` | `{namespace.name}-preprod` | Manual |
| **prod** | `{namespace.cluster_mapping.prod}` | `{namespace.name}` | Manual |

## 🚀 Deployment

### Development
```bash
kubectl apply -f namespaces/{namespace.name}/argocd-apps/dev.yaml
```

### Test
```bash
kubectl apply -f namespaces/{namespace.name}/argocd-apps/test.yaml
```

### PreProduction (Manual)
```bash
kubectl apply -f namespaces/{namespace.name}/argocd-apps/preprod.yaml
argocd app sync {namespace.name}-preprod
```

### Production (Manual)
```bash
kubectl apply -f namespaces/{namespace.name}/argocd-apps/prod.yaml
argocd app sync {namespace.name}-prod
```

## 🔍 Resource Status

```bash
# Development
kubectl get all,pvc,routes,configmaps -n {namespace.name}-dev

# Test
kubectl get all,pvc,routes,configmaps -n {namespace.name}

# PreProduction  
kubectl get all,pvc,routes,configmaps -n {namespace.name}-preprod

# Production
kubectl get all,pvc,routes,configmaps -n {namespace.name}
```

---
*Generated automatically from Minio bucket by minio-to-gitops.py*
"""
        
        readme_file = Path(f"namespaces/{namespace.name}/README.md")
        with open(readme_file, 'w') as f:
            f.write(readme_content)
        
        print(f"📄 Generated README: {readme_file}")
    
    def _generate_root_readme(self) -> None:
        """Generate root README with overview of all namespaces"""
        
        namespace_table = ""
        for ns in self.namespaces:
            resource_count = sum(len(files) for files in ns.resources.values())
            namespace_table += f"| **{ns.name}** | {resource_count} resources | `namespaces/{ns.name}/README.md` |\n"
        
        readme_content = f"""# OpenShift Multi-Cluster GitOps

Auto-generated GitOps structure from Minio bucket with {len(self.namespaces)} namespaces.

## 🏗️ Structure

```
namespaces/
{chr(10).join([f"├── {ns.name}/" for ns in self.namespaces])}
```

## 📦 Namespaces

| Namespace | Resources | Documentation |
|-----------|-----------|---------------|
{namespace_table}

## 🌐 Cluster Mappings

- **dev**: Development environment 
- **test**: Testing environment
- **preprod**: Pre-production environment  
- **prod**: Production environment

## 🚀 Quick Deployment

```bash
# Deploy all dev environments
{chr(10).join([f'kubectl apply -f namespaces/{ns.name}/argocd-apps/dev.yaml' for ns in self.namespaces])}

# Deploy all test environments
{chr(10).join([f'kubectl apply -f namespaces/{ns.name}/argocd-apps/test.yaml' for ns in self.namespaces])}
```

## 📋 Prerequisites

### ArgoCD Cluster Registration
```bash
# Register clusters (update endpoints as needed)
argocd cluster add dev-context --name dev-cluster
argocd cluster add test-context --name test-cluster  
argocd cluster add preprod-context --name preprod-cluster
argocd cluster add prod-context --name prod-cluster
```

---
*Auto-generated by minio-to-gitops.py on {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        
        with open('README.md', 'w') as f:
            f.write(readme_content)
        
        print("📄 Generated root README.md")

def main():
    """Main function to run the Minio to GitOps generator"""
    
    # Configuration - update these values
    minio_config = {
        'endpoint': 'localhost:9000',  # Update with your Minio endpoint
        'access_key': 'minioadmin',
        'secret_key': 'minioadmin123',
        'secure': False,
        'bucket': 'openshift-cluster-backups-4',
        'prefix': 'crc.testing/crc-wrl62'  # Optional prefix path
    }
    
    # Cluster mappings - update with your actual cluster endpoints
    cluster_mappings = {
        'default': {  # Default mapping for all namespaces
            'dev': 'https://dev-cluster-api.example.com',
            'test': 'https://test-cluster-api.example.com',
            'preprod': 'https://preprod-cluster-api.example.com',
            'prod': 'https://prod-cluster-api.example.com'
        },
        # Namespace-specific overrides (optional)
        # 'my-special-namespace': {
        #     'dev': 'https://special-dev-cluster.example.com',
        #     'test': 'https://special-test-cluster.example.com',
        #     'preprod': 'https://special-preprod-cluster.example.com', 
        #     'prod': 'https://special-prod-cluster.example.com'
        # }
    }
    
    git_repo = 'https://github.com/walbis/test.git'  # Update with your Git repo
    
    # Initialize generator
    generator = MinioGitOpsGenerator(minio_config, cluster_mappings, git_repo)
    
    try:
        print("🚀 Starting Minio to GitOps generation...")
        
        # Step 1: Scan Minio bucket to detect namespaces and resources
        generator.scan_minio_bucket()
        
        # Step 2: Download all resources from Minio
        generator.download_resources()
        
        # Step 3: Generate complete GitOps structure
        generator.generate_gitops_structure()
        
        print("🎉 Successfully generated GitOps structure!")
        print(f"📁 Generated {len(generator.namespaces)} namespaces:")
        for ns in generator.namespaces:
            resource_count = sum(len(files) for files in ns.resources.values())
            print(f"   • {ns.name}: {resource_count} resources")
        
        print("\n📋 Next steps:")
        print("1. Review the generated structure")
        print("2. Update cluster endpoints in ArgoCD applications if needed")
        print("3. Commit and push to your Git repository")
        print("4. Register clusters in ArgoCD")
        print("5. Deploy applications using the generated YAML files")
        
    except KeyboardInterrupt:
        print("\n⏹️  Operation cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()