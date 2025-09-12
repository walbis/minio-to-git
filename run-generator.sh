#!/bin/bash

# Minio to GitOps Auto-Generator Runner Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🚀 Minio to GitOps Auto-Generator"
echo "=================================="

# Check if virtual environment exists
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source "$SCRIPT_DIR/venv/bin/activate"

# Install requirements
echo "📥 Installing Python dependencies..."
pip install -q --upgrade pip
pip install -q -r "$SCRIPT_DIR/requirements.txt"

# Check if config file exists
if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    echo "❌ Configuration file not found: $SCRIPT_DIR/config.yaml"
    echo "Please copy and customize config.yaml.example"
    exit 1
fi

echo "📋 Configuration:"
echo "   • Minio endpoint: $(grep 'endpoint:' $SCRIPT_DIR/config.yaml | cut -d'"' -f2)"
echo "   • Bucket: $(grep 'bucket:' $SCRIPT_DIR/config.yaml | cut -d'"' -f2)"
echo "   • Git repo: $(grep 'repository:' $SCRIPT_DIR/config.yaml | cut -d'"' -f2)"

# Change to project root directory
cd "$PROJECT_ROOT"

echo ""
echo "🏗️  Starting generation process..."

# Run the generator script
python3 "$SCRIPT_DIR/minio-to-gitops.py"

echo ""
echo "✅ Generation completed!"
echo ""
echo "📋 Next steps:"
echo "   1. Review the generated namespaces/ directory"
echo "   2. Update cluster endpoints if needed"
echo "   3. Commit changes: git add . && git commit -m 'feat: auto-generated from Minio'"
echo "   4. Push to repository: git push origin main"
echo "   5. Register clusters in ArgoCD"
echo "   6. Deploy applications"

# Deactivate virtual environment
deactivate