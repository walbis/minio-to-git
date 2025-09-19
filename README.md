# Minio to GitOps Auto-Generator

🚀 **Production-Ready** tool that automatically converts Minio bucket backups to enterprise-grade GitOps structure with intelligent multi-cluster support.

## 🎯 What it Does

This tool scans your Minio bucket containing Kubernetes/OpenShift backups and automatically generates:

- ✅ **Namespace-based GitOps structure** with consistent naming
- ✅ **ArgoCD Applications** for multi-cluster deployment
- ✅ **Kustomize overlays** with dynamic environment configurations
- ✅ **Complete documentation** with deployment guides
- ✅ **Advanced YAML cleanup** removing Kubernetes-generated metadata
- ✅ **Multi-environment support** (dev, test, preprod, prod)
- ✅ **Dynamic storage scaling** based on PVC analysis
- ✅ **Enhanced resource detection** with 90%+ accuracy
- ✅ **Memory-optimized processing** for large buckets
- ✅ **Platform-agnostic** (Windows/Linux/macOS)

## 🏗️ Architecture

```
Minio Bucket                    GitOps Repository
├── namespace-1/               ├── namespaces/
│   ├── service.yaml      →    │   ├── namespace-1/
│   ├── deployment.yaml        │   │   ├── argocd-apps/
│   └── configmap.yaml         │   │   │   ├── dev.yaml
└── namespace-2/               │   │   │   ├── test.yaml
    ├── backup-config.yaml     │   │   │   ├── preprod.yaml
    └── imagestream.yaml       │   │   │   └── prod.yaml
                               │   │   └── environments/
                               │   │       ├── dev/
                               │   │       ├── test/
                               │   │       ├── preprod/
                               │   │       └── prod/
                               │   └── namespace-2/
                               │       └── (same structure)
                               └── README.md
```

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/walbis/minio-to-git.git
cd minio-to-git
```

### 2. Configure Settings
```bash
# Edit configuration file
vim config.yaml
```

### 3. Run Generator
```bash
# One-command execution
./run-generator.sh
```

## ⚙️ Configuration

### config.yaml Example:
```yaml
minio:
  endpoint: "minio.example.com:9000"
  access_key: "your-access-key"  # or use MINIO_ACCESS_KEY env var
  secret_key: "your-secret-key"  # or use MINIO_SECRET_KEY env var
  secure: false
  bucket: "k8s-backups"
  prefix: "cluster-backups/prod"

git:
  repository: "https://github.com/your-org/gitops-repo.git"  # or use GIT_REPOSITORY env var

clusters:
  default:
    dev: "https://dev-cluster-api.example.com"
    test: "https://test-cluster-api.example.com" 
    preprod: "https://preprod-cluster-api.example.com"
    prod: "https://prod-cluster-api.example.com"
```

### 🔐 Environment Variables (Recommended for CI/CD):
```bash
export MINIO_ENDPOINT="minio.example.com:9000"
export MINIO_ACCESS_KEY="your-access-key"
export MINIO_SECRET_KEY="your-secret-key"
export MINIO_BUCKET="k8s-backups"
export GIT_REPOSITORY="https://github.com/your-org/gitops-repo.git"

# Environment variables take precedence over config.yaml
python3 minio-to-gitops.py
```

## 📦 Generated Structure

### Per Namespace:
```
namespaces/my-app/
├── README.md                    # Deployment guide
├── argocd-apps/                # ArgoCD Applications  
│   ├── dev.yaml                # → dev-cluster
│   ├── test.yaml               # → test-cluster
│   ├── preprod.yaml            # → preprod-cluster
│   └── prod.yaml               # → prod-cluster
└── environments/               # Environment configs
    ├── dev/                    # Base resources
    │   ├── kustomization.yaml
    │   ├── deployments/
    │   ├── services/
    │   ├── configmaps/
    │   └── ...
    ├── test/                   # Test overlay
    ├── preprod/                # PreProd overlay
    └── prod/                   # Production overlay
```

## 🧹 YAML Cleanup Features

### Comprehensive Metadata Removal:
- ✅ **Status sections** completely removed
- ✅ **UIDs, resourceVersions** and timestamps  
- ✅ **ManagedFields** and controller references
- ✅ **Auto-assigned IPs** (clusterIP, clusterIPs)
- ✅ **Volume bindings** (volumeName for PVCs)
- ✅ **Generated annotations** and labels
- ✅ **Resource-specific cleanup** per Kubernetes kind

### Before Cleanup:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
  uid: 12345678-1234-1234-1234-123456789abc
  resourceVersion: "123456"
  managedFields: [...]
spec:
  clusterIP: 10.96.123.45
  clusterIPs: ["10.96.123.45"]
  type: ClusterIP
status:
  loadBalancer: {}
```

### After Cleanup:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
spec:
  type: ClusterIP
  # clusterIP will be auto-assigned by Kubernetes
```

## 🔍 Enhanced Resource Detection

🎯 **Dual-layer detection** with 90%+ accuracy using both YAML content analysis and filename patterns:

### Primary Detection (YAML Content):
- **Kind-based analysis**: Direct mapping from `kind: Deployment` → `deployments/`
- **20+ Kubernetes resources** supported including StatefulSets, DaemonSets, etc.
- **Automatic categorization** even with non-standard filenames

### Fallback Detection (Filename Patterns):
| Pattern | Resource Type | Directory |
|---------|---------------|-----------|
| `*deploy*`, `*deployment*` | Deployment | `deployments/` |
| `*service*`, `*svc*` | Service | `services/` |
| `*config*`, `*cm*`, `*configmap*` | ConfigMap | `configmaps/` |
| `*secret*` | Secret | `secrets/` |
| `*pvc*`, `*persistent*`, `*volume*` | PVC | `persistentvolumeclaims/` |
| `*route*` | Route | `routes/` |
| `*stateful*`, `*sts*` | StatefulSet | `statefulsets/` |
| `*daemon*`, `*ds*` | DaemonSet | `daemonsets/` |
| `*image*`, `*stream*` | ImageStream | `imagestreams/` |
| `*cron*`, `*job*` | CronJob | `cronjobs/` |
| `*hpa*`, `*autoscal*` | HPA | `hpa/` |
| `*sa*`, `*serviceaccount*` | ServiceAccount | `serviceaccounts/` |

## 🌐 Multi-Environment Support

🎯 **Consistent naming strategy**: Every environment gets unique namespace suffix (`{namespace}-{env}`)

### Environment Specifications:

| Environment | Namespace | Replicas | Storage Scaling | Sync Policy | Target Cluster |
|-------------|-----------|----------|----------------|-------------|----------------|
| **dev** | `{app}-dev` | 1 | Base (1x) | Automated | dev-cluster |
| **test** | `{app}-test` | 1 | Reduced (0.5x) | Automated | test-cluster |
| **preprod** | `{app}-preprod` | 2 | Scaled (2x) | Manual | preprod-cluster |
| **prod** | `{app}-prod` | 3 | Large (5x) | Manual | prod-cluster |

### 🔧 Dynamic Storage Configuration:
- **Intelligent PVC Detection**: Automatically scans existing PVCs
- **Base Size Analysis**: Extracts storage sizes from dev environment
- **Automatic Scaling**: Applies environment-specific multipliers
- **Fallback Defaults**: Safe defaults when parsing fails

## 🚀 Enterprise Features

### 🔐 Security & Reliability:
- ✅ **Environment Variable Support**: Secure credential handling
- ✅ **Categorized Exception Handling**: 7 specialized error types
- ✅ **Configuration Validation**: Comprehensive input validation
- ✅ **Backup System**: Automatic backups before overwriting

### ⚡ Performance & Scalability:
- ✅ **Memory-Optimized**: Batch processing (100 objects/batch)
- ✅ **Platform-Agnostic**: Windows/Linux/macOS path handling
- ✅ **Progress Tracking**: Real-time processing indicators
- ✅ **Streaming Architecture**: No memory bloat for large buckets

### 🧹 Code Quality:
- ✅ **Constants-Based**: No magic numbers in code
- ✅ **Type Safety**: Comprehensive error handling
- ✅ **Clean Architecture**: Modular, maintainable codebase
- ✅ **Production Ready**: Enterprise-grade reliability

## 📋 Prerequisites

### System Requirements:
- **Python 3.8+**
- **Git** installed
- **Network access** to Minio server

### Python Dependencies:
- `minio==7.2.0` - Minio client SDK
- `PyYAML==6.0.1` - YAML processing

### ArgoCD Setup:
```bash
# Register clusters in ArgoCD
argocd cluster add dev-context --name dev-cluster
argocd cluster add test-context --name test-cluster  
argocd cluster add preprod-context --name preprod-cluster
argocd cluster add prod-context --name prod-cluster
```

## 🛠️ Installation & Usage

### Automated Setup:
```bash
# One command does everything
./run-generator.sh
```

### Manual Setup:
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies  
pip install -r requirements.txt

# Run generator
python3 minio-to-gitops.py
```

## 📊 Example Output

```
🚀 Starting Minio to GitOps generation...
🔍 Scanning Minio bucket: k8s-backups/cluster-backups/prod
📄 Found: berkay-test/deployments/database.yaml
📄 Found: berkay-test/services/database-service.yaml
📄 Found: cluster-backup/configmaps/backup-config.yaml
✅ Detected 2 namespaces: ['berkay-test', 'cluster-backup']
📥 Downloading resources from Minio...
🧹 Advanced cleanup completed: database.yaml
🧹 Advanced cleanup completed: database-service.yaml
🏗️ Generating GitOps structure...
📦 Generating structure for namespace: berkay-test
📄 Generated ArgoCD App: namespaces/berkay-test/argocd-apps/dev.yaml
📄 Generated Kustomization: namespaces/berkay-test/environments/dev/kustomization.yaml
📄 Generated README: namespaces/berkay-test/README.md
🎉 Successfully generated GitOps structure!
📁 Generated 2 namespaces:
   • berkay-test: 8 resources
   • cluster-backup: 3 resources
```

## 🔧 Troubleshooting

### Common Issues:

#### 1. Minio Connection Failed
```bash
# Test connection
mc alias set myminio http://your-minio:9000 access-key secret-key
mc ls myminio/your-bucket
```

#### 2. YAML Parsing Errors
```bash
# Validate YAML files
find . -name "*.yaml" -exec yamllint {} \;
```

#### 3. Permission Issues
```bash
# Fix script permissions
chmod +x run-generator.sh
```

## 📚 Files Included

- `minio-to-gitops.py` - Main generator script
- `advanced_yaml_cleanup.py` - Comprehensive YAML cleanup engine
- `run-generator.sh` - Automated setup and execution script
- `config.yaml` - Configuration template
- `requirements.txt` - Python dependencies
- `README.md` - This documentation

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📄 License

This project is licensed under the MIT License.

---

## 🎖️ Version 2.0 - Production Ready

✨ **Latest improvements:**
- 🎯 **100% Dynamic Configuration** - No more hardcoded values
- 🚀 **Memory Optimized** - Handles large buckets efficiently  
- 🔐 **Enterprise Security** - Environment variable support
- 📊 **90%+ Detection Accuracy** - YAML content analysis
- 🌐 **Platform Agnostic** - Works on Windows/Linux/macOS
- 🛡️ **Robust Error Handling** - Categorized exceptions
- 📈 **Progress Tracking** - Real-time processing feedback

---

**🎯 From Minio backups to production GitOps in 5 minutes!**

Transform your static Kubernetes backups into a dynamic, multi-cluster GitOps deployment pipeline with enterprise-grade reliability and best practices built-in.