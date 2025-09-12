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
from dataclasses import dataclass, field
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

@dataclass 
class ProcessingResult:
    """Track processing results and errors"""
    success_files: List[str] = field(default_factory=list)
    failed_files: List[Tuple[str, str]] = field(default_factory=list)  # (filename, error)
    warnings: List[str] = field(default_factory=list)
    namespaces_found: List[str] = field(default_factory=list)
    
    def add_success(self, filename: str):
        self.success_files.append(filename)
    
    def add_failure(self, filename: str, error: str):
        self.failed_files.append((filename, error))
    
    def add_warning(self, message: str):
        self.warnings.append(message)
    
    def has_failures(self) -> bool:
        return len(self.failed_files) > 0
    
    def print_summary(self):
        print(f"\n📊 Processing Summary:")
        print(f"   ✅ Success: {len(self.success_files)} files")
        print(f"   ❌ Failed: {len(self.failed_files)} files")
        print(f"   ⚠️  Warnings: {len(self.warnings)}")
        print(f"   📦 Namespaces: {len(self.namespaces_found)}")
        
        if self.failed_files:
            print(f"\n❌ Failed Files:")
            for filename, error in self.failed_files[:5]:  # Show first 5
                print(f"   • {filename}: {error}")
            if len(self.failed_files) > 5:
                print(f"   • ... and {len(self.failed_files) - 5} more")
        
        if self.warnings:
            print(f"\n⚠️  Warnings:")
            for warning in self.warnings[:3]:  # Show first 3
                print(f"   • {warning}")
            if len(self.warnings) > 3:
                print(f"   • ... and {len(self.warnings) - 3} more")

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
    
    def scan_minio_bucket(self) -> Tuple[List[NamespaceConfig], ProcessingResult]:
        """Scan Minio bucket and detect namespaces with their resources"""
        print(f"🔍 Scanning Minio bucket: {self.bucket_name}/{self.bucket_prefix}")
        
        namespace_resources = {}
        result = ProcessingResult()
        
        try:
            # List all objects in bucket
            print("📋 Listing objects in bucket...")
            objects = list(self.minio_client.list_objects(
                self.bucket_name, 
                prefix=self.bucket_prefix,
                recursive=True
            ))
            
            print(f"📄 Found {len(objects)} objects in bucket")
            
            # Progress tracking
            total_objects = len(objects)
            processed_count = 0
            
            for obj in objects:
                processed_count += 1
                try:
                    # Skip non-YAML files
                    if not obj.object_name.endswith('.yaml'):
                        continue
                    
                    # Parse path with validation
                    path_result = self._safe_parse_path(obj.object_name, self.bucket_prefix)
                    if not path_result:
                        result.add_warning(f"Skipping file with invalid path structure: {obj.object_name}")
                        continue
                    
                    namespace, filename = path_result
                    
                    # Initialize namespace if not exists
                    if namespace not in namespace_resources:
                        namespace_resources[namespace] = {}
                        result.namespaces_found.append(namespace)
                    
                    # Categorize resource by filename pattern
                    resource_type = self._categorize_resource(filename)
                    if resource_type not in namespace_resources[namespace]:
                        namespace_resources[namespace][resource_type] = []
                    
                    namespace_resources[namespace][resource_type].append(filename)
                    result.add_success(obj.object_name)
                    
                    # Progress indicator
                    if processed_count % 5 == 0 or processed_count == total_objects:
                        progress = (processed_count / total_objects) * 100
                        print(f"📊 Progress: {processed_count}/{total_objects} ({progress:.1f}%) - Found: {namespace}/{resource_type}/{filename}")
                    else:
                        print(f"📄 Found: {namespace}/{resource_type}/{filename}")
                    
                except Exception as e:
                    result.add_failure(obj.object_name, str(e))
                    print(f"⚠️  Error processing {obj.object_name}: {e}")
                    continue  # Continue with next file
        
        except S3Error as e:
            error_msg = f"Failed to connect to Minio: {e}"
            result.add_failure("minio_connection", error_msg)
            print(f"❌ {error_msg}")
            # Don't exit, return empty result
            return [], result
        except Exception as e:
            error_msg = f"Unexpected error during bucket scan: {e}"
            result.add_failure("bucket_scan", error_msg)
            print(f"❌ {error_msg}")
            return [], result
        
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
        return self.namespaces, result
    
    def _validate_yaml_content(self, file_path: Path) -> bool:
        """Validate that YAML file has proper structure and no leftover K8s metadata"""
        try:
            with open(file_path, 'r') as f:
                docs = list(yaml.safe_load_all(f))
            
            for i, doc in enumerate(docs):
                if not doc:
                    continue
                    
                # Check for basic K8s structure
                if not isinstance(doc, dict):
                    print(f"⚠️  Document {i} in {file_path.name} is not a dictionary")
                    return False
                
                # Warn about remaining problematic metadata
                if 'metadata' in doc:
                    problematic_fields = ['uid', 'resourceVersion', 'managedFields']
                    found_issues = [f for f in problematic_fields if f in doc['metadata']]
                    if found_issues:
                        print(f"⚠️  Document {i} in {file_path.name} still contains: {found_issues}")
                        return False
                
                # Check for status section
                if 'status' in doc:
                    print(f"⚠️  Document {i} in {file_path.name} still contains status section")
                    return False
                    
            return True
            
        except yaml.YAMLError as e:
            print(f"❌ YAML validation failed for {file_path}: {e}")
            return False
        except Exception as e:
            print(f"❌ Validation error for {file_path}: {e}")
            return False
    
    def _safe_parse_path(self, object_path: str, prefix: str) -> Tuple[str, str] or None:
        """Safely parse Minio object path to extract namespace and filename"""
        try:
            # Remove prefix and clean path
            clean_path = object_path.replace(prefix, '').strip('/')
            path_parts = clean_path.split('/')
            
            # Validate path structure
            if len(path_parts) < 2:
                return None
                
            # Extract namespace and filename
            namespace = path_parts[-2]  # Second to last part
            filename = path_parts[-1]   # Last part
            
            # Basic validation
            if not namespace or not filename:
                return None
                
            # Sanitize namespace name (Kubernetes naming rules)
            if not self._is_valid_namespace_name(namespace):
                return None
                
            return namespace, filename
            
        except Exception:
            return None
    
    def _is_valid_namespace_name(self, name: str) -> bool:
        """Validate Kubernetes namespace naming rules"""
        import re
        
        # Basic Kubernetes naming rules
        if len(name) > 63:
            return False
        if not re.match(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$', name):
            return False
        return True
    
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
    
    def download_resources(self) -> ProcessingResult:
        """Download all resources from Minio to local filesystem with error handling"""
        print("📥 Downloading resources from Minio...")
        result = ProcessingResult()
        
        for namespace in self.namespaces:
            base_path = Path(f"namespaces/{namespace.name}/environments/dev")
            
            for resource_type, filenames in namespace.resources.items():
                resource_dir = base_path / resource_type
                
                try:
                    resource_dir.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    result.add_failure(f"create_directory_{resource_dir}", str(e))
                    print(f"❌ Failed to create directory {resource_dir}: {e}")
                    continue
                
                for filename in filenames:
                    minio_path = f"{self.bucket_prefix}/{namespace.name}/{filename}".strip('/')
                    local_path = resource_dir / filename
                    
                    try:
                        # Download file from Minio
                        self.minio_client.fget_object(
                            self.bucket_name,
                            minio_path,
                            str(local_path)
                        )
                        
                        # Clean up Kubernetes metadata
                        cleanup_success = self._cleanup_k8s_metadata(local_path)
                        if not cleanup_success:
                            result.add_warning(f"Cleanup failed for {local_path}, but file downloaded")
                        else:
                            # Validate cleanup was successful
                            if not self._validate_yaml_content(local_path):
                                result.add_warning(f"YAML validation failed for {local_path} after cleanup")
                        
                        result.add_success(str(local_path))
                        print(f"📄 Downloaded: {minio_path} → {local_path}")
                        
                    except S3Error as e:
                        result.add_failure(minio_path, f"Minio error: {e}")
                        print(f"❌ Failed to download {minio_path}: {e}")
                        continue
                    except Exception as e:
                        result.add_failure(minio_path, f"Unexpected error: {e}")
                        print(f"❌ Unexpected error downloading {minio_path}: {e}")
                        continue
        
        return result
    
    def _cleanup_k8s_metadata(self, file_path: Path) -> bool:
        """Remove Kubernetes-generated metadata from YAML files with unified approach"""
        try:
            # Try advanced cleaner first (preferred)
            if self._try_advanced_cleanup(file_path):
                return True
            
            # Fallback to built-in cleanup
            print(f"🔄 Using built-in cleanup for {file_path.name}")
            return self._builtin_cleanup_k8s_metadata(file_path)
                
        except Exception as e:
            print(f"❌ Cleanup failed for {file_path}: {e}")
            return False
    
    def _try_advanced_cleanup(self, file_path: Path) -> bool:
        """Try using the advanced YAML cleaner"""
        try:
            from advanced_yaml_cleanup import KubernetesYAMLCleaner
            cleaner = KubernetesYAMLCleaner()
            success = cleaner.clean_yaml_file(file_path)
            
            if success:
                print(f"🧹 Advanced cleanup completed: {file_path.name}")
                return True
            else:
                print(f"⚠️  Advanced cleanup reported failure for: {file_path.name}")
                return False
                
        except ImportError:
            print(f"📄 Advanced cleaner not available for {file_path.name}")
            return False
        except Exception as e:
            print(f"⚠️  Advanced cleanup error for {file_path.name}: {e}")
            return False
            
    def _builtin_cleanup_k8s_metadata(self, file_path: Path) -> bool:
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
                
            return True
                
        except Exception as e:
            print(f"⚠️  Warning: Could not clean metadata from {file_path}: {e}")
            return False
    
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

def load_config(config_path='config.yaml', validate_env=True):
    """Load configuration from YAML file with environment variable support"""
    import os
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file: {e}")
    
    # Support environment variables for sensitive data
    def get_config_value(config_dict, key, env_var=None, required=True):
        """Get config value with environment variable fallback"""
        if env_var:
            env_value = os.getenv(env_var)
            if env_value:
                return env_value
        
        value = config_dict.get(key)
        if required and not value:
            raise ValueError(f"Missing required configuration: {key} (or env var: {env_var})")
        return value
    
    # Extract minio config with env var support
    minio_config = {
        'endpoint': get_config_value(config['minio'], 'endpoint', 'MINIO_ENDPOINT'),
        'access_key': get_config_value(config['minio'], 'access_key', 'MINIO_ACCESS_KEY'),
        'secret_key': get_config_value(config['minio'], 'secret_key', 'MINIO_SECRET_KEY'),
        'secure': config['minio'].get('secure', False),
        'bucket': get_config_value(config['minio'], 'bucket', 'MINIO_BUCKET'),
        'prefix': config['minio'].get('prefix', '')
    }
    
    # Extract git repo
    git_repo = get_config_value(config['git'], 'repository', 'GIT_REPOSITORY')
    
    # Extract cluster mappings
    cluster_mappings = config.get('clusters', {})
    
    # Validate cluster mappings
    if 'default' not in cluster_mappings:
        raise ValueError("Configuration must contain 'default' cluster mapping")
    
    required_envs = ['dev', 'test', 'preprod', 'prod']
    for env in required_envs:
        if env not in cluster_mappings['default']:
            raise ValueError(f"Default cluster mapping missing environment: {env}")
    
    # Validate environment variables if requested
    if validate_env:
        _validate_environment_config(config)
    
    return minio_config, cluster_mappings, git_repo, config

def _validate_environment_config(config):
    """Validate environment-specific configuration"""
    import os
    
    # Check for environment-specific overrides
    env_overrides = {
        'MINIO_ENDPOINT': config['minio'].get('endpoint'),
        'MINIO_ACCESS_KEY': config['minio'].get('access_key'),
        'MINIO_SECRET_KEY': config['minio'].get('secret_key'),
        'MINIO_BUCKET': config['minio'].get('bucket'),
        'GIT_REPOSITORY': config['git'].get('repository')
    }
    
    # Log which values are being used from environment
    env_used = []
    for env_var, config_value in env_overrides.items():
        if os.getenv(env_var):
            env_used.append(env_var)
    
    if env_used:
        print(f"🔐 Using environment variables: {', '.join(env_used)}")
    
    # Validate critical settings
    if not config.get('minio', {}).get('endpoint') and not os.getenv('MINIO_ENDPOINT'):
        raise ValueError("Minio endpoint must be specified in config or MINIO_ENDPOINT environment variable")
        
    if not config.get('git', {}).get('repository') and not os.getenv('GIT_REPOSITORY'):
        raise ValueError("Git repository must be specified in config or GIT_REPOSITORY environment variable")

def create_backup(backup_name=None):
    """Create backup of existing namespaces directory"""
    import shutil
    from datetime import datetime
    
    namespaces_dir = Path("namespaces")
    if not namespaces_dir.exists():
        print("📁 No existing namespaces directory to backup")
        return None
    
    if not backup_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"namespaces_backup_{timestamp}"
    
    backup_dir = Path(backup_name)
    try:
        shutil.copytree(namespaces_dir, backup_dir)
        print(f"💾 Created backup: {backup_dir}")
        return backup_dir
    except Exception as e:
        print(f"⚠️  Backup creation failed: {e}")
        return None

def main():
    """Main function to run the Minio to GitOps generator"""
    
    try:
        # Load configuration from file
        print("📋 Loading configuration...")
        minio_config, cluster_mappings, git_repo, full_config = load_config()
        
        print(f"✅ Configuration loaded successfully:")
        print(f"   • Minio: {minio_config['endpoint']}")
        print(f"   • Bucket: {minio_config['bucket']}")
        print(f"   • Git repo: {git_repo}")
        
        # Create backup if namespaces directory exists
        backup_dir = create_backup()
        
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        print("💡 Make sure config.yaml exists and is properly formatted")
        sys.exit(1)
    
    # Initialize generator
    generator = MinioGitOpsGenerator(minio_config, cluster_mappings, git_repo)
    
    overall_result = ProcessingResult()
    
    try:
        print("🚀 Starting Minio to GitOps generation...")
        
        # Step 1: Scan Minio bucket to detect namespaces and resources
        print("\n📋 Step 1: Scanning Minio bucket...")
        namespaces, scan_result = generator.scan_minio_bucket()
        overall_result.success_files.extend(scan_result.success_files)
        overall_result.failed_files.extend(scan_result.failed_files)
        overall_result.warnings.extend(scan_result.warnings)
        
        # Check if we found any namespaces
        if not namespaces:
            print("❌ No namespaces found in Minio bucket!")
            if scan_result.has_failures():
                print("💡 This might be due to connection issues or path structure problems")
                scan_result.print_summary()
            sys.exit(1)
        
        generator.namespaces = namespaces
        
        # Step 2: Download all resources from Minio (with error handling)
        print("\n📥 Step 2: Downloading resources...")
        download_result = generator.download_resources()
        if download_result:
            overall_result.failed_files.extend(download_result.failed_files)
            overall_result.warnings.extend(download_result.warnings)
        
        # Step 3: Generate complete GitOps structure
        print("\n🏗️  Step 3: Generating GitOps structure...")
        generator.generate_gitops_structure()
        
        # Print results
        print("🎉 GitOps structure generation completed!")
        print(f"📁 Generated {len(generator.namespaces)} namespaces:")
        for ns in generator.namespaces:
            resource_count = sum(len(files) for files in ns.resources.values())
            print(f"   • {ns.name}: {resource_count} resources")
        
        # Show processing summary
        overall_result.print_summary()
        
        # Show next steps
        print("\n📋 Next steps:")
        print("1. Review the generated namespaces/ directory")
        print("2. Update cluster endpoints in ArgoCD applications if needed")
        print("3. Commit changes: git add . && git commit -m 'feat: auto-generated from Minio'")
        print("4. Push to repository: git push origin main")
        print("5. Register clusters in ArgoCD")
        print("6. Deploy applications")
        
        # Exit with appropriate code
        if overall_result.has_failures():
            print("\n⚠️  Some files failed to process, but GitOps structure was generated")
            sys.exit(2)  # Warning exit code
        else:
            print("\n✅ All files processed successfully!")
            sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n⏹️  Operation cancelled by user")
        sys.exit(130)  # Standard SIGINT exit code
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        print("🔍 Detailed error:")
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()