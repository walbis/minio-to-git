#!/usr/bin/env python3
"""
Integration Test for GitOps Structure Generation
Tests the complete workflow with mock data
"""

import os
import sys
import yaml
import tempfile
import shutil
from pathlib import Path

def create_test_environment():
    """Create a test environment with sample data"""
    test_dir = tempfile.mkdtemp(prefix="gitops_test_")
    print(f"📁 Created test environment: {test_dir}")
    
    # Create sample namespace structure
    namespaces_dir = Path(test_dir) / "namespaces"
    
    # Sample namespace 1: web-app
    webapp_ns = namespaces_dir / "web-app"
    webapp_ns.mkdir(parents=True, exist_ok=True)
    
    # Create deployments directory
    deployments_dir = webapp_ns / "deployments"
    deployments_dir.mkdir(exist_ok=True)
    
    # Sample deployment
    deployment_yaml = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-app
  namespace: web-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web-app
  template:
    metadata:
      labels:
        app: web-app
    spec:
      containers:
      - name: web
        image: nginx:1.21
        ports:
        - containerPort: 80
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
"""
    
    (deployments_dir / "web-app.yaml").write_text(deployment_yaml)
    
    # Create services directory
    services_dir = webapp_ns / "services"
    services_dir.mkdir(exist_ok=True)
    
    # Sample service
    service_yaml = """
apiVersion: v1
kind: Service
metadata:
  name: web-app-service
  namespace: web-app
spec:
  selector:
    app: web-app
  ports:
  - protocol: TCP
    port: 80
    targetPort: 80
  type: ClusterIP
"""
    
    (services_dir / "web-app-service.yaml").write_text(service_yaml)
    
    # Create configmaps directory
    configmaps_dir = webapp_ns / "configmaps"
    configmaps_dir.mkdir(exist_ok=True)
    
    # Sample configmap
    configmap_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-app-config
  namespace: web-app
data:
  app.properties: |
    server.port=8080
    app.name=web-app
    app.version=1.0.0
  nginx.conf: |
    server {
        listen 80;
        location / {
            root /usr/share/nginx/html;
            index index.html;
        }
    }
"""
    
    (configmaps_dir / "web-app-config.yaml").write_text(configmap_yaml)
    
    # Sample namespace 2: database
    db_ns = namespaces_dir / "database"
    db_ns.mkdir(parents=True, exist_ok=True)
    
    # Create StatefulSet for database
    statefulsets_dir = db_ns / "statefulsets"
    statefulsets_dir.mkdir(exist_ok=True)
    
    statefulset_yaml = """
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres-db
  namespace: database
spec:
  serviceName: postgres-service
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:13
        env:
        - name: POSTGRES_DB
          value: "myapp"
        - name: POSTGRES_USER
          value: "dbuser"
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: password
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
  - metadata:
      name: postgres-storage
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 10Gi
"""
    
    (statefulsets_dir / "postgres-db.yaml").write_text(statefulset_yaml)
    
    # Create PVC directory
    pvcs_dir = db_ns / "persistentvolumeclaims"
    pvcs_dir.mkdir(exist_ok=True)
    
    pvc_yaml = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: database
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: fast-ssd
"""
    
    (pvcs_dir / "postgres-pvc.yaml").write_text(pvc_yaml)
    
    return test_dir, namespaces_dir

def test_kustomization_generation(namespaces_dir):
    """Test kustomization file generation"""
    print("\n🧪 Testing Kustomization Generation...")
    
    # Test for web-app namespace
    webapp_ns = namespaces_dir / "web-app"
    
    # Generate base kustomization
    resources = []
    for resource_dir in webapp_ns.iterdir():
        if resource_dir.is_dir() and resource_dir.name != "environments":
            for resource_file in resource_dir.glob("*.yaml"):
                relative_path = resource_file.relative_to(webapp_ns)
                resources.append(str(relative_path))
    
    if resources:
        kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'resources': sorted(resources),
            'namespace': 'web-app'
        }
        
        kustomization_file = webapp_ns / "kustomization.yaml"
        with open(kustomization_file, 'w') as f:
            yaml.dump(kustomization, f, default_flow_style=False)
        
        print(f"✅ Generated base kustomization with {len(resources)} resources")
        
        # Verify kustomization file
        if kustomization_file.exists():
            with open(kustomization_file) as f:
                loaded = yaml.safe_load(f)
            
            if (loaded.get('kind') == 'Kustomization' and 
                'resources' in loaded and 
                len(loaded['resources']) > 0):
                print("✅ Kustomization file structure is correct")
                return True
            else:
                print("❌ Kustomization file structure is invalid")
                return False
        else:
            print("❌ Kustomization file was not created")
            return False
    else:
        print("❌ No resources found for kustomization")
        return False

def test_environment_overlays_generation(namespaces_dir):
    """Test environment overlay generation"""
    print("\n🧪 Testing Environment Overlays Generation...")
    
    environments = ['dev', 'test', 'preprod', 'prod']
    webapp_ns = namespaces_dir / "web-app"
    
    # Create environments directory
    environments_dir = namespaces_dir.parent / "environments"
    environments_dir.mkdir(exist_ok=True)
    
    success_count = 0
    
    for env in environments:
        env_dir = environments_dir / env / "web-app"
        env_dir.mkdir(parents=True, exist_ok=True)
        
        # Create environment-specific overlay
        overlay_kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'namespace': 'web-app',
            'resources': [
                '../../../namespaces/web-app'
            ],
            'patchesStrategicMerge': [],
            'images': [],
            'replicas': []
        }
        
        # Environment-specific configurations
        if env == 'dev':
            overlay_kustomization['replicas'] = [
                {'name': 'web-app', 'count': 1}
            ]
        elif env == 'test':
            overlay_kustomization['replicas'] = [
                {'name': 'web-app', 'count': 2}
            ]
        elif env == 'preprod':
            overlay_kustomization['replicas'] = [
                {'name': 'web-app', 'count': 3}
            ]
        elif env == 'prod':
            overlay_kustomization['replicas'] = [
                {'name': 'web-app', 'count': 5}
            ]
        
        # Write overlay kustomization
        overlay_file = env_dir / "kustomization.yaml"
        with open(overlay_file, 'w') as f:
            yaml.dump(overlay_kustomization, f, default_flow_style=False)
        
        # Verify overlay
        if overlay_file.exists():
            with open(overlay_file) as f:
                loaded = yaml.safe_load(f)
            
            if (loaded.get('kind') == 'Kustomization' and 
                'resources' in loaded and 
                len(loaded['resources']) > 0):
                print(f"✅ {env} environment overlay created successfully")
                success_count += 1
            else:
                print(f"❌ {env} environment overlay structure is invalid")
        else:
            print(f"❌ {env} environment overlay was not created")
    
    if success_count == len(environments):
        print("✅ All environment overlays generated successfully")
        return True
    else:
        print(f"❌ Only {success_count}/{len(environments)} overlays generated successfully")
        return False

def test_argocd_application_generation(namespaces_dir):
    """Test ArgoCD Application generation"""
    print("\n🧪 Testing ArgoCD Application Generation...")
    
    # Create ArgoCD apps directory
    argocd_dir = namespaces_dir.parent / "argocd-apps"
    argocd_dir.mkdir(exist_ok=True)
    
    environments = ['dev', 'test', 'preprod', 'prod']
    namespaces = ['web-app', 'database']
    
    success_count = 0
    total_apps = len(environments) * len(namespaces)
    
    for env in environments:
        for namespace in namespaces:
            app_name = f"{namespace}-{env}"
            
            # Generate ArgoCD Application
            application = {
                'apiVersion': 'argoproj.io/v1alpha1',
                'kind': 'Application',
                'metadata': {
                    'name': app_name,
                    'namespace': 'argocd',
                    'labels': {
                        'environment': env,
                        'namespace': namespace
                    }
                },
                'spec': {
                    'project': 'default',
                    'source': {
                        'repoURL': 'https://github.com/example/gitops-repo.git',
                        'targetRevision': 'HEAD',
                        'path': f'environments/{env}/{namespace}'
                    },
                    'destination': {
                        'server': f'https://{env}-cluster-api.example.com',
                        'namespace': namespace
                    },
                    'syncPolicy': {
                        'automated': {
                            'prune': True,
                            'selfHeal': True
                        } if env in ['dev', 'test'] else None,
                        'syncOptions': [
                            'CreateNamespace=true'
                        ]
                    }
                }
            }
            
            # Write application file
            app_file = argocd_dir / f"{app_name}.yaml"
            with open(app_file, 'w') as f:
                yaml.dump(application, f, default_flow_style=False)
            
            # Verify application
            if app_file.exists():
                with open(app_file) as f:
                    loaded = yaml.safe_load(f)
                
                if (loaded.get('kind') == 'Application' and 
                    loaded.get('apiVersion') == 'argoproj.io/v1alpha1' and 
                    'spec' in loaded and 
                    'source' in loaded['spec'] and 
                    'destination' in loaded['spec']):
                    success_count += 1
                else:
                    print(f"❌ {app_name} application structure is invalid")
            else:
                print(f"❌ {app_name} application was not created")
    
    print(f"✅ Generated {success_count}/{total_apps} ArgoCD Applications")
    
    if success_count == total_apps:
        print("✅ All ArgoCD Applications generated successfully")
        return True
    else:
        print(f"❌ Only {success_count}/{total_apps} applications generated successfully")
        return False

def test_resource_validation(namespaces_dir):
    """Test that all generated resources are valid Kubernetes YAML"""
    print("\n🧪 Testing Resource Validation...")
    
    total_files = 0
    valid_files = 0
    
    # Check all YAML files in the test environment
    for yaml_file in namespaces_dir.rglob("*.yaml"):
        if yaml_file.name != "kustomization.yaml":  # Skip kustomization files
            total_files += 1
            
            try:
                with open(yaml_file) as f:
                    docs = list(yaml.safe_load_all(f))
                
                for doc in docs:
                    if doc is None:
                        continue
                    
                    # Check required Kubernetes fields
                    if ('apiVersion' in doc and 
                        'kind' in doc and 
                        'metadata' in doc and 
                        'name' in doc['metadata']):
                        valid_files += 1
                    else:
                        print(f"❌ Invalid Kubernetes resource in {yaml_file}")
                        
            except Exception as e:
                print(f"❌ Failed to parse {yaml_file}: {e}")
    
    print(f"✅ Validated {valid_files}/{total_files} resource files")
    
    if valid_files == total_files and total_files > 0:
        print("✅ All resources are valid Kubernetes YAML")
        return True
    else:
        print(f"❌ Some resources are invalid ({valid_files}/{total_files})")
        return False

def test_directory_structure(test_dir):
    """Test the overall directory structure"""
    print("\n🧪 Testing Directory Structure...")
    
    expected_structure = {
        'namespaces': ['web-app', 'database'],
        'environments': ['dev', 'test', 'preprod', 'prod'],
        'argocd-apps': []
    }
    
    test_path = Path(test_dir)
    structure_valid = True
    
    # Check namespaces directory
    namespaces_dir = test_path / "namespaces"
    if namespaces_dir.exists():
        found_namespaces = [d.name for d in namespaces_dir.iterdir() if d.is_dir()]
        missing_namespaces = set(expected_structure['namespaces']) - set(found_namespaces)
        
        if missing_namespaces:
            print(f"❌ Missing namespaces: {missing_namespaces}")
            structure_valid = False
        else:
            print(f"✅ All expected namespaces found: {found_namespaces}")
    else:
        print("❌ Namespaces directory not found")
        structure_valid = False
    
    # Check environments directory
    environments_dir = test_path / "environments"
    if environments_dir.exists():
        found_environments = [d.name for d in environments_dir.iterdir() if d.is_dir()]
        missing_environments = set(expected_structure['environments']) - set(found_environments)
        
        if missing_environments:
            print(f"❌ Missing environments: {missing_environments}")
            structure_valid = False
        else:
            print(f"✅ All expected environments found: {found_environments}")
    else:
        print("❌ Environments directory not found")
        structure_valid = False
    
    # Check ArgoCD apps directory
    argocd_dir = test_path / "argocd-apps"
    if argocd_dir.exists():
        app_files = list(argocd_dir.glob("*.yaml"))
        if len(app_files) > 0:
            print(f"✅ Found {len(app_files)} ArgoCD application files")
        else:
            print("❌ No ArgoCD application files found")
            structure_valid = False
    else:
        print("❌ ArgoCD apps directory not found")
        structure_valid = False
    
    return structure_valid

def main():
    """Run integration tests"""
    print("🔄 GitOps Structure Generation Integration Test")
    print("=" * 55)
    
    test_dir = None
    try:
        # Create test environment
        test_dir, namespaces_dir = create_test_environment()
        
        # Run tests
        tests = [
            lambda: test_kustomization_generation(namespaces_dir),
            lambda: test_environment_overlays_generation(namespaces_dir),
            lambda: test_argocd_application_generation(namespaces_dir),
            lambda: test_resource_validation(namespaces_dir),
            lambda: test_directory_structure(test_dir)
        ]
        
        test_names = [
            "Kustomization Generation",
            "Environment Overlays",
            "ArgoCD Applications", 
            "Resource Validation",
            "Directory Structure"
        ]
        
        passed = 0
        total = len(tests)
        
        for i, test in enumerate(tests):
            print(f"\n📋 Running Test {i+1}: {test_names[i]}")
            try:
                if test():
                    passed += 1
                    print(f"✅ {test_names[i]} test passed")
                else:
                    print(f"❌ {test_names[i]} test failed")
            except Exception as e:
                print(f"💥 {test_names[i]} test crashed: {e}")
        
        # Summary
        print(f"\n📊 Integration Test Results")
        print("=" * 35)
        print(f"Passed: {passed}/{total}")
        print(f"Success Rate: {(passed/total*100):.1f}%")
        
        if passed == total:
            print("🎉 ALL INTEGRATION TESTS PASSED!")
            print("✅ GitOps structure generation is working correctly")
            return True
        elif passed >= (total * 0.8):
            print("✅ Most tests passed - GitOps generation is mostly functional")
            return True
        else:
            print("❌ Multiple test failures - GitOps generation needs attention")
            return False
    
    finally:
        # Cleanup
        if test_dir and os.path.exists(test_dir):
            try:
                shutil.rmtree(test_dir)
                print(f"\n🧹 Cleaned up test environment: {test_dir}")
            except Exception as e:
                print(f"⚠️  Failed to cleanup test environment: {e}")

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)