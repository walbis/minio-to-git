#!/usr/bin/env python3
"""
Advanced YAML Cleanup for Kubernetes Resources
Comprehensive cleanup of all Kubernetes-generated fields
"""

import yaml
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

class KubernetesYAMLCleaner:
    """Advanced Kubernetes YAML cleaner with comprehensive field removal"""
    
    # Metadata fields that should NEVER be in GitOps
    METADATA_BLACKLIST = {
        'uid', 'resourceVersion', 'generation', 'creationTimestamp',
        'deletionTimestamp', 'deletionGracePeriodSeconds', 'managedFields',
        'selfLink', 'finalizers', 'ownerReferences'
    }
    
    # Annotation keys that should be removed (Kubernetes-generated)
    ANNOTATION_BLACKLIST = {
        'kubectl.kubernetes.io/last-applied-configuration',
        'deployment.kubernetes.io/revision', 
        'control-plane.alpha.kubernetes.io/leader',
        'pv.kubernetes.io/bind-completed',
        'pv.kubernetes.io/bound-by-controller',
        'volume.beta.kubernetes.io/storage-provisioner',
        'volume.kubernetes.io/storage-provisioner'
    }
    
    # Label keys that should be removed (Kubernetes-generated)
    LABEL_BLACKLIST = {
        'pod-template-hash',
        'controller-revision-hash',
        'statefulset.kubernetes.io/pod-name'
    }
    
    # Resource-specific fields to remove
    RESOURCE_SPECIFIC_CLEANUP = {
        'Service': {
            'spec': ['clusterIP', 'clusterIPs', 'internalTrafficPolicy', 'externalTrafficPolicy']
        },
        'PersistentVolumeClaim': {
            'spec': ['volumeName'],
            'metadata': ['finalizers']
        },
        'Deployment': {
            'spec': ['revisionHistoryLimit'],  # Keep this configurable
            'metadata': ['generation']
        },
        'Pod': {
            'spec': ['nodeName', 'serviceAccount'],  # serviceAccount auto-generated
            'metadata': ['generateName']
        },
        'ReplicaSet': {
            'metadata': ['ownerReferences']  # Already in general blacklist
        }
    }

    def __init__(self, preserve_fields: Optional[List[str]] = None):
        """
        Initialize cleaner with optional field preservation
        
        Args:
            preserve_fields: List of fields to preserve even if blacklisted
        """
        self.preserve_fields = set(preserve_fields or [])
    
    def clean_yaml_file(self, file_path: Path, backup: bool = False) -> bool:
        """
        Clean a single YAML file
        
        Args:
            file_path: Path to YAML file
            backup: Create .backup file before cleaning
            
        Returns:
            bool: True if cleaned successfully, False otherwise
        """
        try:
            # Create backup if requested
            if backup:
                backup_path = file_path.with_suffix(f"{file_path.suffix}.backup")
                backup_path.write_text(file_path.read_text())
                print(f"💾 Created backup: {backup_path}")
            
            # Load all documents from file
            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    docs = list(yaml.safe_load_all(f))
                except yaml.YAMLError as e:
                    print(f"⚠️  YAML parsing error in {file_path}: {e}")
                    return False
            
            # Clean each document
            cleaned_docs = []
            for i, doc in enumerate(docs):
                if not doc:  # Skip empty documents
                    continue
                
                cleaned_doc = self.clean_document(doc)
                if cleaned_doc:
                    cleaned_docs.append(cleaned_doc)
                    print(f"🧹 Cleaned document {i+1} in {file_path.name}")
            
            if not cleaned_docs:
                print(f"⚠️  No valid documents found in {file_path}")
                return False
            
            # Write cleaned documents back
            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump_all(
                    cleaned_docs, 
                    f, 
                    default_flow_style=False, 
                    sort_keys=False,
                    allow_unicode=True,
                    width=120,
                    indent=2
                )
            
            print(f"✅ Successfully cleaned {file_path}")
            return True
            
        except Exception as e:
            print(f"❌ Error cleaning {file_path}: {e}")
            return False
    
    def clean_document(self, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Clean a single Kubernetes document
        
        Args:
            doc: Kubernetes resource document
            
        Returns:
            Cleaned document or None if invalid
        """
        if not isinstance(doc, dict):
            return None
        
        # Skip documents without basic K8s structure
        if 'apiVersion' not in doc or 'kind' not in doc:
            print("⚠️  Skipping non-Kubernetes document")
            return doc  # Return as-is, might be config file
        
        kind = doc.get('kind', '')
        
        # 1. Remove status section entirely
        doc.pop('status', None)
        
        # 2. Clean metadata
        if 'metadata' in doc:
            doc['metadata'] = self._clean_metadata(doc['metadata'])
        
        # 3. Clean spec based on resource type
        if 'spec' in doc and kind in self.RESOURCE_SPECIFIC_CLEANUP:
            doc['spec'] = self._clean_spec(doc['spec'], kind)
        
        # 4. Resource-specific cleaning
        doc = self._resource_specific_cleanup(doc, kind)
        
        return doc
    
    def _clean_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Clean metadata section"""
        if not isinstance(metadata, dict):
            return metadata
        
        # Remove blacklisted fields
        for field in self.METADATA_BLACKLIST:
            if field not in self.preserve_fields:
                metadata.pop(field, None)
        
        # Clean annotations
        if 'annotations' in metadata and metadata['annotations']:
            metadata['annotations'] = self._clean_dict(
                metadata['annotations'], 
                self.ANNOTATION_BLACKLIST
            )
            # Remove empty annotations dict
            if not metadata['annotations']:
                metadata.pop('annotations', None)
        
        # Clean labels  
        if 'labels' in metadata and metadata['labels']:
            metadata['labels'] = self._clean_dict(
                metadata['labels'],
                self.LABEL_BLACKLIST
            )
        
        return metadata
    
    def _clean_spec(self, spec: Dict[str, Any], kind: str) -> Dict[str, Any]:
        """Clean spec section based on resource kind"""
        if not isinstance(spec, dict):
            return spec
        
        if kind in self.RESOURCE_SPECIFIC_CLEANUP:
            cleanup_rules = self.RESOURCE_SPECIFIC_CLEANUP[kind]
            if 'spec' in cleanup_rules:
                for field in cleanup_rules['spec']:
                    if field not in self.preserve_fields:
                        spec.pop(field, None)
        
        return spec
    
    def _clean_dict(self, d: Dict[str, Any], blacklist: set) -> Dict[str, Any]:
        """Remove blacklisted keys from dictionary"""
        if not isinstance(d, dict):
            return d
        
        return {k: v for k, v in d.items() if k not in blacklist}
    
    def _resource_specific_cleanup(self, doc: Dict[str, Any], kind: str) -> Dict[str, Any]:
        """Perform resource-specific cleanup"""
        
        # Service-specific cleanup
        if kind == 'Service':
            spec = doc.get('spec', {})
            # Remove auto-assigned cluster IPs
            spec.pop('clusterIP', None)
            spec.pop('clusterIPs', None)
            # Remove health check node port for LoadBalancer
            if spec.get('type') == 'LoadBalancer':
                spec.pop('healthCheckNodePort', None)
        
        # PVC-specific cleanup
        elif kind == 'PersistentVolumeClaim':
            spec = doc.get('spec', {})
            # Remove volume name (bound by controller)
            spec.pop('volumeName', None)
            # Remove volume mode if default
            if spec.get('volumeMode') == 'Filesystem':
                spec.pop('volumeMode', None)
        
        # Deployment-specific cleanup
        elif kind == 'Deployment':
            spec = doc.get('spec', {})
            # Remove observed generation
            spec.pop('observedGeneration', None)
            # Clean template metadata
            if 'template' in spec and 'metadata' in spec['template']:
                spec['template']['metadata'] = self._clean_metadata(
                    spec['template']['metadata']
                )
        
        # ConfigMap/Secret specific
        elif kind in ['ConfigMap', 'Secret']:
            # Remove data keys that look like Kubernetes-generated
            data = doc.get('data', {})
            generated_patterns = [
                r'ca\.crt$',
                r'service-ca\.crt$',
                r'ca-bundle\.crt$'
            ]
            
            keys_to_check = list(data.keys())
            for key in keys_to_check:
                for pattern in generated_patterns:
                    if re.match(pattern, key):
                        print(f"🔍 Found potential generated data key: {key} (keeping)")
                        break
        
        return doc
    
    def clean_directory(self, directory: Path, pattern: str = "*.yaml", recursive: bool = True) -> None:
        """
        Clean all YAML files in a directory
        
        Args:
            directory: Directory to clean
            pattern: File pattern to match
            recursive: Clean subdirectories
        """
        if recursive:
            yaml_files = directory.rglob(pattern)
        else:
            yaml_files = directory.glob(pattern)
        
        success_count = 0
        total_count = 0
        
        for yaml_file in yaml_files:
            if yaml_file.is_file():
                total_count += 1
                if self.clean_yaml_file(yaml_file):
                    success_count += 1
        
        print(f"🎯 Cleaned {success_count}/{total_count} YAML files in {directory}")

    def validate_cleanup(self, file_path: Path) -> Dict[str, Any]:
        """
        Validate that cleanup was successful
        
        Returns:
            Dict with validation results
        """
        try:
            with open(file_path, 'r') as f:
                docs = list(yaml.safe_load_all(f))
            
            issues = []
            for i, doc in enumerate(docs):
                if not doc:
                    continue
                
                # Check for remaining blacklisted fields
                if 'metadata' in doc:
                    for field in self.METADATA_BLACKLIST:
                        if field in doc['metadata']:
                            issues.append(f"Document {i}: Found {field} in metadata")
                
                # Check for status
                if 'status' in doc:
                    issues.append(f"Document {i}: Status section still present")
            
            return {
                'file': str(file_path),
                'valid': len(issues) == 0,
                'issues': issues,
                'document_count': len([d for d in docs if d])
            }
            
        except Exception as e:
            return {
                'file': str(file_path),
                'valid': False,
                'issues': [f"Validation error: {e}"],
                'document_count': 0
            }

def main():
    """Example usage of the YAML cleaner"""
    
    # Initialize cleaner
    cleaner = KubernetesYAMLCleaner()
    
    # Clean specific file
    # cleaner.clean_yaml_file(Path("example.yaml"), backup=True)
    
    # Clean entire directory
    # cleaner.clean_directory(Path("namespaces/"), recursive=True)
    
    print("🧹 YAML cleaner ready!")
    print("Usage examples:")
    print("  cleaner.clean_yaml_file(Path('file.yaml'))")
    print("  cleaner.clean_directory(Path('namespaces/'))")

if __name__ == '__main__':
    main()