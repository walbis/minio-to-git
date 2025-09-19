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
import time
import urllib3

# Custom Exceptions for better error handling
class MinioGitOpsError(Exception):
    """Base exception for Minio GitOps generator errors"""
    pass

class ConfigurationError(MinioGitOpsError):
    """Configuration related errors"""
    pass

class MinioConnectionError(MinioGitOpsError):
    """Minio connection and access errors"""
    pass

class YAMLProcessingError(MinioGitOpsError):
    """YAML parsing and processing errors"""
    pass

class PathValidationError(MinioGitOpsError):
    """Path structure validation errors"""
    pass

class NamespaceValidationError(MinioGitOpsError):
    """Namespace naming validation errors"""
    pass

class StorageConfigurationError(MinioGitOpsError):
    """Storage configuration and detection errors"""
    pass

class ValidationError(MinioGitOpsError):
    """Input validation errors"""
    pass

class SecurityError(MinioGitOpsError):
    """Security-related validation errors"""
    pass

class FileSizeError(ValidationError):
    """File size limit exceeded errors"""
    pass

class ContentValidationError(ValidationError):
    """Content validation errors"""
    pass

class NetworkError(MinioGitOpsError):
    """Network-related errors"""
    pass

class TimeoutError(NetworkError):
    """Timeout errors"""
    pass

class RetryExhaustedError(NetworkError):
    """All retry attempts failed"""
    pass

# Configuration Constants
class Constants:
    # Display and Logging Configuration
    MAX_FAILED_FILES_DISPLAY = 5
    MAX_WARNINGS_DISPLAY = 3
    PROGRESS_UPDATE_INTERVAL = 5  # Show progress every N files
    
    # Path Validation
    MIN_PATH_PARTS = 2
    MAX_NAMESPACE_LENGTH = 63
    
    # Storage Scaling Factors
    STORAGE_SCALE_TEST = 0.5    # Half size for test
    STORAGE_SCALE_PREPROD = 2   # 2x size for preprod  
    STORAGE_SCALE_PROD = 5      # 5x size for prod
    
    # Default Storage Sizes (fallback)
    DEFAULT_STORAGE_TEST = '1Gi'
    DEFAULT_STORAGE_PREPROD = '10Gi'
    DEFAULT_STORAGE_PROD = '50Gi'
    
    # Replica Configuration
    DEFAULT_REPLICAS_TEST = 1
    DEFAULT_REPLICAS_PREPROD = 2
    DEFAULT_REPLICAS_PROD = 3
    
    # Progress Display
    PROGRESS_DECIMAL_PLACES = 1
    
    # Memory Management
    MEMORY_BATCH_SIZE = 100  # Process objects in batches to control memory usage
    
    # Default Environment Configuration
    DEFAULT_ENVIRONMENTS = ['dev', 'test', 'preprod', 'prod']
    
    # Directory Structure
    BASE_NAMESPACE_DIR = 'namespaces'
    ENVIRONMENTS_DIR = 'environments' 
    ARGOCD_APPS_DIR = 'argocd-apps'
    DEFAULT_BASE_ENV = 'dev'
    
    # Input Validation and Security Limits
    MAX_FILE_SIZE_MB = 50  # Maximum YAML file size in MB
    MAX_FILES_PER_NAMESPACE = 1000  # Maximum files per namespace
    MAX_NAMESPACES = 100  # Maximum namespaces to process
    MAX_YAML_DEPTH = 20  # Maximum YAML nesting depth
    MAX_STRING_LENGTH = 10000  # Maximum string field length
    MAX_LIST_ITEMS = 1000  # Maximum list items
    MAX_CONFIG_SIZE_MB = 10  # Maximum config file size
    
    # Kubernetes Naming Constraints
    MAX_KUBERNETES_NAME_LENGTH = 253
    MIN_KUBERNETES_NAME_LENGTH = 1
    
    # Content Validation Patterns
    ALLOWED_YAML_EXTENSIONS = ['.yaml', '.yml']
    DANGEROUS_PATTERNS = [
        'eval(',
        'exec(',
        '__import__',
        'subprocess',
        'os.system',
        '${',  # Template injection
        '{{',  # Template injection
    ]
    
    # Network Security and Resilience
    MAX_MINIO_TIMEOUT = 300  # 5 minutes max timeout
    MAX_RETRY_ATTEMPTS = 3
    INITIAL_RETRY_DELAY = 1.0  # Initial delay between retries (seconds)
    MAX_RETRY_DELAY = 30.0     # Maximum delay between retries (seconds)
    RETRY_BACKOFF_FACTOR = 2.0 # Exponential backoff multiplier
    CONNECTION_TIMEOUT = 10.0  # Connection timeout (seconds)
    READ_TIMEOUT = 60.0        # Read timeout (seconds)
    
    # Minio Connection Pool Settings
    MAX_POOL_CONNECTIONS = 10  # Maximum connections in pool
    MAX_POOL_SIZE = 20         # Maximum pool size

def retry_with_exponential_backoff(max_attempts=None, initial_delay=None, max_delay=None, backoff_factor=None):
    """Decorator for retrying functions with exponential backoff"""
    if max_attempts is None:
        max_attempts = Constants.MAX_RETRY_ATTEMPTS
    if initial_delay is None:
        initial_delay = Constants.INITIAL_RETRY_DELAY
    if max_delay is None:
        max_delay = Constants.MAX_RETRY_DELAY
    if backoff_factor is None:
        backoff_factor = Constants.RETRY_BACKOFF_FACTOR
    
    def decorator(func):
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (S3Error, OSError, urllib3.exceptions.HTTPError, Exception) as e:
                    last_exception = e
                    
                    # Don't retry on certain errors
                    if isinstance(e, (ValidationError, SecurityError, FileSizeError)):
                        raise e
                    
                    if attempt == max_attempts - 1:
                        break
                    
                    print(f"⚠️  Attempt {attempt + 1}/{max_attempts} failed: {e}. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    
                    # Exponential backoff with jitter
                    delay = min(delay * backoff_factor, max_delay)
                    # Add small jitter to prevent thundering herd
                    import random
                    delay += random.uniform(0, delay * 0.1)
            
            raise RetryExhaustedError(f"All {max_attempts} retry attempts failed. Last error: {last_exception}")
        
        return wrapper
    return decorator

def timeout_handler(timeout_seconds=None):
    """Decorator to add timeout handling to functions"""
    if timeout_seconds is None:
        timeout_seconds = Constants.MAX_MINIO_TIMEOUT
    
    def decorator(func):
        def wrapper(*args, **kwargs):
            import signal
            
            def timeout_signal_handler(signum, frame):
                raise TimeoutError(f"Function '{func.__name__}' timed out after {timeout_seconds} seconds")
            
            # Set the signal handler and alarm
            old_handler = signal.signal(signal.SIGALRM, timeout_signal_handler)
            signal.alarm(int(timeout_seconds))
            
            try:
                result = func(*args, **kwargs)
                signal.alarm(0)  # Disable the alarm
                return result
            except TimeoutError:
                print(f"⏰ Operation timed out after {timeout_seconds} seconds")
                raise
            finally:
                signal.signal(signal.SIGALRM, old_handler)  # Restore old handler
        
        return wrapper
    return decorator

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
            for filename, error in self.failed_files[:Constants.MAX_FAILED_FILES_DISPLAY]:
                print(f"   • {filename}: {error}")
            if len(self.failed_files) > Constants.MAX_FAILED_FILES_DISPLAY:
                print(f"   • ... and {len(self.failed_files) - Constants.MAX_FAILED_FILES_DISPLAY} more")
        
        if self.warnings:
            print(f"\n⚠️  Warnings:")
            for warning in self.warnings[:Constants.MAX_WARNINGS_DISPLAY]:
                print(f"   • {warning}")
            if len(self.warnings) > Constants.MAX_WARNINGS_DISPLAY:
                print(f"   • ... and {len(self.warnings) - Constants.MAX_WARNINGS_DISPLAY} more")

class MinioGitOpsGenerator:
    def __init__(self, minio_config: dict, cluster_mappings: dict, git_repo: str, 
                 environments: List[str] = None, base_env: str = None):
        # Configure connection pool for better performance and reliability
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(
                connect=Constants.CONNECTION_TIMEOUT,
                read=Constants.READ_TIMEOUT
            ),
            maxsize=Constants.MAX_POOL_CONNECTIONS,
            block=False,
            retries=urllib3.Retry(
                total=Constants.MAX_RETRY_ATTEMPTS,
                backoff_factor=Constants.RETRY_BACKOFF_FACTOR,
                status_forcelist=[429, 500, 502, 503, 504]
            )
        )
        
        self.minio_client = Minio(
            minio_config['endpoint'],
            access_key=minio_config['access_key'],
            secret_key=minio_config['secret_key'],
            secure=minio_config.get('secure', False),
            http_client=http_client
        )
        
        # Test connection on initialization
        self._test_minio_connection()
        self.bucket_name = minio_config['bucket']
        self.bucket_prefix = minio_config.get('prefix', '')
        self.git_repo = git_repo
        self.cluster_mappings = cluster_mappings
        self.namespaces: List[NamespaceConfig] = []
        
        # Configurable environments
        self.environments = environments or Constants.DEFAULT_ENVIRONMENTS
        self.base_env = base_env or Constants.DEFAULT_BASE_ENV
        
        # Validate environments exist in cluster mappings
        self._validate_environment_configuration()
    
    def _validate_environment_configuration(self):
        """Validate that all environments exist in cluster mappings"""
        default_clusters = self.cluster_mappings.get('default', {})
        
        missing_envs = []
        for env in self.environments:
            if env not in default_clusters:
                missing_envs.append(env)
        
        if missing_envs:
            raise ConfigurationError(f"Missing cluster mappings for environments: {missing_envs}")
        
        if self.base_env not in self.environments:
            raise ConfigurationError(f"Base environment '{self.base_env}' not in configured environments: {self.environments}")
            
        print(f"✅ Environment configuration validated: {self.environments} (base: {self.base_env})")
    
    @retry_with_exponential_backoff(max_attempts=2)  # Quick test, only 2 attempts
    def _test_minio_connection(self):
        """Test Minio connection during initialization"""
        try:
            # Simple connectivity test
            buckets = list(self.minio_client.list_buckets())
            print(f"✅ Minio connection successful. Found {len(buckets)} buckets.")
        except Exception as e:
            print(f"❌ Minio connection test failed: {e}")
            raise MinioConnectionError(f"Failed to connect to Minio: {e}")
    
    @retry_with_exponential_backoff()
    def _resilient_list_objects(self, bucket_name: str, prefix: str = "", recursive: bool = True):
        """List objects with retry logic and timeout handling"""
        try:
            objects = self.minio_client.list_objects(
                bucket_name, 
                prefix=prefix,
                recursive=recursive
            )
            return objects
        except Exception as e:
            print(f"⚠️  Error listing objects from bucket '{bucket_name}' with prefix '{prefix}': {e}")
            raise NetworkError(f"Failed to list objects: {e}")
    
    @retry_with_exponential_backoff()
    def _resilient_get_object(self, bucket_name: str, object_name: str):
        """Get object with retry logic and timeout handling"""
        try:
            response = self.minio_client.get_object(bucket_name, object_name)
            return response
        except Exception as e:
            print(f"⚠️  Error downloading object '{object_name}' from bucket '{bucket_name}': {e}")
            raise NetworkError(f"Failed to download object: {e}")
    
    def _safe_write_file(self, file_path: Path, content: str, description: str = "file"):
        """Safely write content to file with comprehensive error handling"""
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Check available disk space (basic check)
            import shutil
            free_space = shutil.disk_usage(file_path.parent).free
            content_size = len(content.encode('utf-8'))
            
            # Require at least 100MB free space or 10x content size, whichever is larger
            min_free_space = max(100 * 1024 * 1024, content_size * 10)
            if free_space < min_free_space:
                raise OSError(f"Insufficient disk space for {description}: {free_space / (1024*1024):.1f}MB free")
            
            # Write content with atomic operation (write to temp then rename)
            temp_path = file_path.with_suffix('.tmp')
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    f.flush()  # Ensure content is written
                    os.fsync(f.fileno())  # Force OS to write to disk
                
                # Atomic rename
                temp_path.replace(file_path)
                print(f"✅ Generated {description}: {file_path}")
                
            except Exception as e:
                # Clean up temp file if something went wrong
                if temp_path.exists():
                    temp_path.unlink()
                raise e
                
        except PermissionError as e:
            raise OSError(f"Permission denied writing {description} to {file_path}: {e}")
        except OSError as e:
            raise OSError(f"OS error writing {description} to {file_path}: {e}")
        except Exception as e:
            raise Exception(f"Unexpected error writing {description} to {file_path}: {e}")
    
    def _safe_read_file(self, file_path: Path, description: str = "file") -> str:
        """Safely read file content with comprehensive error handling"""
        try:
            # Check file exists and is readable
            if not file_path.exists():
                raise FileNotFoundError(f"{description.capitalize()} does not exist: {file_path}")
            
            if not file_path.is_file():
                raise ValueError(f"Path is not a file: {file_path}")
            
            # Check file size before opening
            file_size = file_path.stat().st_size
            self._validate_file_size(file_size, str(file_path))
            
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Validate content
            self._validate_yaml_content(content, str(file_path))
            return content
            
        except FileNotFoundError as e:
            raise FileNotFoundError(f"File not found: {e}")
        except PermissionError as e:
            raise PermissionError(f"Permission denied reading {description} {file_path}: {e}")
        except UnicodeDecodeError as e:
            raise ValueError(f"Invalid file encoding for {description} {file_path}: {e}")
        except OSError as e:
            raise OSError(f"OS error reading {description} {file_path}: {e}")
        except Exception as e:
            raise Exception(f"Unexpected error reading {description} {file_path}: {e}")
    
    def _validate_file_size(self, file_size: int, filename: str):
        """Validate file size against security limits"""
        max_size_bytes = Constants.MAX_FILE_SIZE_MB * 1024 * 1024
        if file_size > max_size_bytes:
            raise FileSizeError(f"File '{filename}' exceeds maximum size limit ({Constants.MAX_FILE_SIZE_MB}MB)")
    
    def _validate_filename(self, filename: str):
        """Validate filename for security and format"""
        if not filename:
            raise ValidationError("Empty filename provided")
        
        # Check extension
        file_path = Path(filename)
        if file_path.suffix.lower() not in Constants.ALLOWED_YAML_EXTENSIONS:
            raise ValidationError(f"Invalid file extension: {file_path.suffix}. Allowed: {Constants.ALLOWED_YAML_EXTENSIONS}")
        
        # Check for dangerous patterns in filename
        filename_lower = filename.lower()
        for pattern in Constants.DANGEROUS_PATTERNS:
            if pattern in filename_lower:
                raise SecurityError(f"Potentially dangerous pattern '{pattern}' found in filename: {filename}")
    
    def _validate_yaml_content(self, content: str, filename: str):
        """Validate YAML content for security and size limits"""
        if not content or not content.strip():
            raise ContentValidationError(f"Empty or whitespace-only content in file: {filename}")
        
        # Check content size
        content_size = len(content.encode('utf-8'))
        max_size_bytes = Constants.MAX_FILE_SIZE_MB * 1024 * 1024
        if content_size > max_size_bytes:
            raise FileSizeError(f"Content size exceeds limit for file '{filename}': {content_size / (1024*1024):.1f}MB")
        
        # Check for dangerous patterns in content
        content_lower = content.lower()
        for pattern in Constants.DANGEROUS_PATTERNS:
            if pattern in content_lower:
                raise SecurityError(f"Potentially dangerous pattern '{pattern}' found in content of: {filename}")
    
    def _validate_yaml_structure(self, data: dict, filename: str, depth: int = 0):
        """Recursively validate YAML structure for security limits"""
        if depth > Constants.MAX_YAML_DEPTH:
            raise ValidationError(f"YAML nesting depth exceeds limit ({Constants.MAX_YAML_DEPTH}) in file: {filename}")
        
        if isinstance(data, dict):
            if len(data) > Constants.MAX_LIST_ITEMS:
                raise ValidationError(f"Dictionary size exceeds limit ({Constants.MAX_LIST_ITEMS}) in file: {filename}")
            
            for key, value in data.items():
                if isinstance(key, str) and len(key) > Constants.MAX_STRING_LENGTH:
                    raise ValidationError(f"Key length exceeds limit in file: {filename}")
                
                if isinstance(value, str) and len(value) > Constants.MAX_STRING_LENGTH:
                    raise ValidationError(f"String value length exceeds limit in file: {filename}")
                
                if isinstance(value, (dict, list)):
                    self._validate_yaml_structure(value, filename, depth + 1)
                    
        elif isinstance(data, list):
            if len(data) > Constants.MAX_LIST_ITEMS:
                raise ValidationError(f"List size exceeds limit ({Constants.MAX_LIST_ITEMS}) in file: {filename}")
            
            for item in data:
                if isinstance(item, (dict, list)):
                    self._validate_yaml_structure(item, filename, depth + 1)
                elif isinstance(item, str) and len(item) > Constants.MAX_STRING_LENGTH:
                    raise ValidationError(f"List item string length exceeds limit in file: {filename}")
    
    def _validate_kubernetes_name(self, name: str, resource_type: str = "resource"):
        """Validate Kubernetes resource names"""
        if not name:
            raise ValidationError(f"Empty {resource_type} name")
        
        if len(name) < Constants.MIN_KUBERNETES_NAME_LENGTH:
            raise ValidationError(f"{resource_type} name too short: {name}")
        
        if len(name) > Constants.MAX_KUBERNETES_NAME_LENGTH:
            raise ValidationError(f"{resource_type} name too long ({len(name)} > {Constants.MAX_KUBERNETES_NAME_LENGTH}): {name}")
        
        # Basic Kubernetes naming validation
        import re
        if not re.match(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$', name):
            raise ValidationError(f"Invalid {resource_type} name format: {name}. Must be lowercase alphanumeric with hyphens.")
    
    def _validate_namespace_limits(self, namespaces: dict):
        """Validate namespace count and file limits"""
        if len(namespaces) > Constants.MAX_NAMESPACES:
            raise ValidationError(f"Namespace count exceeds limit ({Constants.MAX_NAMESPACES}): {len(namespaces)}")
        
        for namespace, files in namespaces.items():
            if len(files) > Constants.MAX_FILES_PER_NAMESPACE:
                raise ValidationError(f"File count in namespace '{namespace}' exceeds limit ({Constants.MAX_FILES_PER_NAMESPACE}): {len(files)}")
    
    @timeout_handler(timeout_seconds=Constants.MAX_MINIO_TIMEOUT)
    def scan_minio_bucket(self) -> Tuple[List[NamespaceConfig], ProcessingResult]:
        """Scan Minio bucket and detect namespaces with their resources"""
        print(f"🔍 Scanning Minio bucket: {self.bucket_name}/{self.bucket_prefix}")
        
        namespace_resources = {}
        result = ProcessingResult()
        
        try:
            # Memory-optimized batch processing approach
            print("📋 Processing objects in memory-efficient batches...")
            
            batch_size = Constants.MEMORY_BATCH_SIZE if hasattr(Constants, 'MEMORY_BATCH_SIZE') else 100
            batch = []
            processed_count = 0
            total_objects = 0  # We'll count as we go
            
            # Process objects in batches to control memory usage with resilient connection
            for obj in self._resilient_list_objects(
                self.bucket_name, 
                prefix=self.bucket_prefix,
                recursive=True
            ):
                total_objects += 1
                batch.append(obj)
                
                # Process batch when it's full
                if len(batch) >= batch_size:
                    processed_count += self._process_object_batch(batch, namespace_resources, result, processed_count, total_objects)
                    batch.clear()  # Clear batch to free memory
            
            # Process final batch
            if batch:
                processed_count += self._process_object_batch(batch, namespace_resources, result, processed_count, total_objects)
                
            print(f"📄 Total processed: {processed_count}/{total_objects} objects")
        
        except S3Error as e:
            error_msg = f"Failed to connect to Minio: {e}"
            result.add_failure("minio_connection", error_msg)
            print(f"❌ {error_msg}")
            # Don't exit, return empty result
            return [], result
        except MinioConnectionError as e:
            result.add_failure("minio_connection", str(e))
            print(f"❌ Minio Connection Error: {e}")
            return [], result
        except TimeoutError as e:
            result.add_failure("timeout", str(e))
            print(f"⏰ Timeout Error: {e}")
            return [], result
        except RetryExhaustedError as e:
            result.add_failure("retry_exhausted", str(e))
            print(f"🔄 Retry Exhausted: {e}")
            return [], result
        except NetworkError as e:
            result.add_failure("network_error", str(e))
            print(f"🌐 Network Error: {e}")
            return [], result
        except Exception as e:
            error_msg = f"Unexpected error during bucket scan: {e}"
            result.add_failure("bucket_scan", error_msg)
            print(f"❌ {error_msg}")
            return [], result
        
        # Validate namespace and file limits
        try:
            self._validate_namespace_limits(namespace_resources)
        except ValidationError as e:
            result.add_failure("validation", str(e))
            print(f"❌ Validation Error: {e}")
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
    
    def _process_object_batch(self, batch: list, namespace_resources: dict, result: ProcessingResult, 
                             start_count: int, total_objects: int) -> int:
        """Process a batch of objects in memory-efficient way"""
        batch_processed = 0
        
        for obj in batch:
            current_count = start_count + batch_processed + 1
            try:
                # Validate filename format and security
                self._validate_filename(obj.object_name)
                
                # Validate file size before processing
                if hasattr(obj, 'size') and obj.size:
                    self._validate_file_size(obj.size, obj.object_name)
                
                # Parse path with validation
                path_result = self._safe_parse_path(obj.object_name, self.bucket_prefix)
                if not path_result:
                    result.add_warning(f"Skipping file with invalid path structure: {obj.object_name}")
                    continue
                
                namespace, filename = path_result
                
                # Validate namespace name
                self._validate_kubernetes_name(namespace, "namespace")
                
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
                if current_count % Constants.PROGRESS_UPDATE_INTERVAL == 0 or current_count == total_objects:
                    progress = (current_count / total_objects) * 100
                    print(f"📊 Progress: {current_count}/{total_objects} ({progress:.{Constants.PROGRESS_DECIMAL_PLACES}f}%) - Found: {namespace}/{resource_type}/{filename}")
                elif current_count % 20 == 0:  # Show less frequent updates to reduce noise
                    print(f"📄 Found: {namespace}/{resource_type}/{filename}")
                
                batch_processed += 1
                
            except Exception as e:
                result.add_failure(obj.object_name, str(e))
                print(f"⚠️  Error processing {obj.object_name}: {e}")
                continue  # Continue with next file
        
        return batch_processed
    
    def _validate_yaml_content_file(self, file_path: Path) -> bool:
        """Validate that YAML file has proper structure and no leftover K8s metadata"""
        try:
            # Check file exists and is readable
            if not file_path.exists():
                raise FileNotFoundError(f"YAML file does not exist: {file_path}")
            
            if not file_path.is_file():
                raise ValueError(f"Path is not a file: {file_path}")
            
            # Check file size before opening
            file_size = file_path.stat().st_size
            self._validate_file_size(file_size, str(file_path))
            
            with open(file_path, 'r', encoding='utf-8') as f:
                docs = list(yaml.safe_load_all(f))
                
        except FileNotFoundError as e:
            print(f"❌ File not found: {e}")
            return False
        except PermissionError as e:
            print(f"❌ Permission denied accessing file {file_path}: {e}")
            return False
        except UnicodeDecodeError as e:
            print(f"❌ Invalid file encoding for {file_path}: {e}")
            return False
        except OSError as e:
            print(f"❌ OS error reading file {file_path}: {e}")
            return False
        except yaml.YAMLError as e:
            print(f"❌ YAML parsing error in {file_path}: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error validating {file_path}: {e}")
            return False
        
        try:
            
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
            raise YAMLProcessingError(f"YAML validation failed for {file_path}: {e}") from e
        except Exception as e:
            print(f"❌ Validation error for {file_path}: {e}")
            raise YAMLProcessingError(f"Validation error for {file_path}: {e}") from e
    
    def _safe_parse_path(self, object_path: str, prefix: str) -> Tuple[str, str] or None:
        """Platform-agnostic path parsing for Minio object paths"""
        try:
            # Import platform-specific utilities
            import os
            from pathlib import PurePosixPath  # Minio always uses POSIX paths
            
            # Remove prefix and clean path - always use forward slashes for Minio
            clean_path = object_path.replace(prefix, '').strip('/')
            
            # Normalize path separators - Minio uses POSIX style regardless of platform
            clean_path = clean_path.replace('\\', '/')
            
            # Use PurePosixPath for consistent parsing across platforms
            path_obj = PurePosixPath(clean_path)
            path_parts = path_obj.parts
            
            # Enhanced path structure validation
            if len(path_parts) < Constants.MIN_PATH_PARTS:
                raise PathValidationError(f"Path too short: {object_path} (need at least {Constants.MIN_PATH_PARTS} parts)")
            
            # Safe array access with bounds checking
            try:
                # Extract namespace and filename with bounds protection
                if len(path_parts) >= 2:
                    namespace = path_parts[-2]  # Second to last part
                    filename = path_parts[-1]   # Last part
                else:
                    raise PathValidationError(f"Insufficient path components: {path_parts}")
            except IndexError as e:
                raise PathValidationError(f"Path parsing error for {object_path}: {e}") from e
            
            # Basic validation
            if not namespace or not filename:
                return None
                
            # Sanitize namespace name (Kubernetes naming rules)
            if not self._is_valid_namespace_name(namespace):
                raise NamespaceValidationError(f"Invalid namespace name: {namespace}")
                
            return namespace, filename
            
        except Exception as e:
            print(f"⚠️  Path parsing error for {object_path}: {e}")
            return None
    
    def _is_valid_namespace_name(self, name: str) -> bool:
        """Validate Kubernetes namespace naming rules"""
        import re
        
        # Basic Kubernetes naming rules
        if len(name) > Constants.MAX_NAMESPACE_LENGTH:
            return False
        if not re.match(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$', name):
            return False
        return True
    
    def _categorize_resource(self, filename: str, file_content: str = None) -> str:
        """Enhanced resource categorization using both filename and YAML content"""
        
        # First try YAML content analysis if available
        if file_content:
            try:
                category = self._categorize_by_yaml_content(file_content, filename)
                if category != 'other':
                    return category
            except Exception as e:
                print(f"⚠️  Could not parse YAML content for {filename}: {e}")
                
        # Fallback to enhanced filename analysis
        return self._categorize_by_filename(filename)
    
    def _categorize_by_yaml_content(self, content: str, filename: str = "unknown") -> str:
        """Categorize resource by analyzing YAML content"""
        try:
            # Validate YAML content first
            self._validate_yaml_content(content, filename)
            
            docs = list(yaml.safe_load_all(content))
            for doc in docs:
                if not doc or 'kind' not in doc:
                    continue
                
                # Validate YAML structure for security limits
                self._validate_yaml_structure(doc, filename)
                    
                kind = doc['kind'].lower()
                
                # Direct kind mapping
                kind_mapping = {
                    'deployment': 'deployments',
                    'service': 'services', 
                    'configmap': 'configmaps',
                    'secret': 'secrets',
                    'persistentvolumeclaim': 'persistentvolumeclaims',
                    'route': 'routes',
                    'ingress': 'ingress',
                    'cronjob': 'cronjobs',
                    'job': 'jobs',
                    'horizontalpodautoscaler': 'hpa',
                    'imagestream': 'imagestreams',
                    'networkpolicy': 'networkpolicies',
                    'statefulset': 'statefulsets',
                    'daemonset': 'daemonsets',
                    'replicaset': 'replicasets',
                    'pod': 'pods',
                    'namespace': 'namespaces',
                    'role': 'roles',
                    'rolebinding': 'rolebindings',
                    'clusterrole': 'clusterroles',
                    'clusterrolebinding': 'clusterrolebindings',
                    'serviceaccount': 'serviceaccounts'
                }
                
                if kind in kind_mapping:
                    return kind_mapping[kind]
                    
            return 'other'
            
        except yaml.YAMLError:
            return 'other'
    
    def _categorize_by_filename(self, filename: str) -> str:
        """Enhanced filename-based categorization with more patterns"""
        filename_lower = filename.lower()
        
        # More comprehensive filename patterns
        patterns = {
            'deployments': ['deploy', 'deployment'],
            'services': ['service', 'svc'],
            'configmaps': ['config', 'cm', 'configmap'],
            'secrets': ['secret'],
            'persistentvolumeclaims': ['pvc', 'persistent', 'volume', 'claim'],
            'routes': ['route'],
            'ingress': ['ingress'],
            'cronjobs': ['cron', 'cronjob'],
            'jobs': ['job'],
            'hpa': ['hpa', 'autoscal', 'horizontal'],
            'imagestreams': ['image', 'stream', 'imagestream'],
            'networkpolicies': ['network', 'policy', 'netpol'],
            'statefulsets': ['stateful', 'sts'],
            'daemonsets': ['daemon', 'ds'],
            'replicasets': ['replica', 'rs'],
            'pods': ['pod'],
            'namespaces': ['namespace', 'ns'],
            'roles': ['role'],
            'rolebindings': ['rolebind', 'binding'],
            'serviceaccounts': ['serviceaccount', 'sa']
        }
        
        for category, keywords in patterns.items():
            if any(keyword in filename_lower for keyword in keywords):
                return category
                
        # Still return 'other' but log for investigation
        print(f"⚠️  Unrecognized resource type for file: {filename}")
        return 'other'
    
    def download_resources(self) -> ProcessingResult:
        """Download all resources from Minio to local filesystem with error handling"""
        print("📥 Downloading resources from Minio...")
        result = ProcessingResult()
        
        for namespace in self.namespaces:
            base_path = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / Constants.ENVIRONMENTS_DIR / self.base_env
            
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
                        
                        # Enhanced categorization using downloaded content
                        try:
                            with open(local_path, 'r') as f:
                                file_content = f.read()
                            
                            # Re-categorize with content analysis
                            better_category = self._categorize_resource(local_path.name, file_content)
                            if better_category != resource_type:
                                print(f"🔍 Improved categorization: {local_path.name} {resource_type} → {better_category}")
                                # Update category if needed (would require refactoring, for now just log)
                                
                        except Exception as e:
                            print(f"⚠️  Could not re-categorize {local_path.name}: {e}")
                        
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
        """Fallback simple cleanup method with enhanced error handling"""
        try:
            # Use safe file reading
            content = self._safe_read_file(file_path, "YAML file for cleanup")
            docs = list(yaml.safe_load_all(content))
            
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
            
            # Use safe file writing
            cleaned_content = yaml.dump_all(cleaned_docs, default_flow_style=False, sort_keys=False)
            self._safe_write_file(file_path, cleaned_content, "cleaned YAML file")
            
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
        ns_path = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name
        (ns_path / Constants.ARGOCD_APPS_DIR).mkdir(parents=True, exist_ok=True)
        
        for env in self.environments:
            (ns_path / Constants.ENVIRONMENTS_DIR / env).mkdir(parents=True, exist_ok=True)
        
        # Generate ArgoCD Applications
        self._generate_argocd_apps(namespace)
        
        # Generate Kustomizations
        self._generate_kustomizations(namespace)
        
        # Generate namespace README
        self._generate_namespace_readme(namespace)
    
    def _generate_argocd_apps(self, namespace: NamespaceConfig) -> None:
        """Generate ArgoCD Application manifests for all environments"""
        apps_path = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / Constants.ARGOCD_APPS_DIR
        
        # CONSISTENT NAMING: Every environment gets its own namespace suffix
        environments = {}
        for env in self.environments:
            # Get cluster mapping for this environment
            cluster = getattr(namespace.cluster_mapping, env)
            
            # Determine sync policy (automated for first 2 envs, manual for others)
            is_automated = self.environments.index(env) < 2
            sync_policy = {
                'automated': {
                    'prune': is_automated, 
                    'selfHeal': is_automated
                }
            }
            
            environments[env] = {
                'cluster': cluster,
                'target_namespace': f"{namespace.name}-{env}",
                'sync_policy': sync_policy
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
    
    def _detect_pvc_storage_requirements(self, namespace: NamespaceConfig) -> Dict[str, Dict[str, str]]:
        """Dynamically detect PVC storage requirements from namespace resources"""
        storage_configs = {
            'test': {},
            'preprod': {},
            'prod': {}
        }
        
        # Check if namespace has PVCs
        if 'persistentvolumeclaims' not in namespace.resources:
            return storage_configs
        
        # Scan actual PVC files to extract names and base storage sizes
        pvc_dir = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / Constants.ENVIRONMENTS_DIR / self.base_env / "persistentvolumeclaims"
        if not pvc_dir.exists():
            return storage_configs
        
        detected_pvcs = []
        for pvc_file in pvc_dir.glob("*.yaml"):
            try:
                # Use safe file reading for PVC files
                content = self._safe_read_file(pvc_file, "PVC file")
                docs = list(yaml.safe_load_all(content))
                for doc in docs:
                        if doc and doc.get('kind') == 'PersistentVolumeClaim':
                            pvc_name = self._safe_get_pvc_name(doc, pvc_file.stem)
                            base_size = self._safe_get_storage_size(doc)
                            detected_pvcs.append((pvc_name, base_size))
            except yaml.YAMLError as e:
                print(f"⚠️  YAML parsing error in PVC file {pvc_file}: {e}")
                # Fallback to filename
                detected_pvcs.append((pvc_file.stem, Constants.DEFAULT_STORAGE_TEST))
            except Exception as e:
                print(f"⚠️  Could not parse PVC file {pvc_file}: {e}")
                # Fallback to filename  
                detected_pvcs.append((pvc_file.stem, Constants.DEFAULT_STORAGE_TEST))
        
        # Generate environment-specific storage configurations
        for pvc_name, base_size in detected_pvcs:
            # Parse base size to create scaled versions
            try:
                import re
                size_match = re.match(r'(\d+)(\w+)', base_size)
                if size_match:
                    base_num = int(size_match.group(1))
                    unit = size_match.group(2)
                    
                    # Scale storage based on environment using constants
                    storage_configs['test'][pvc_name] = f"{max(1, int(base_num * Constants.STORAGE_SCALE_TEST))}{unit}"
                    storage_configs['preprod'][pvc_name] = f"{base_num * Constants.STORAGE_SCALE_PREPROD}{unit}"
                    storage_configs['prod'][pvc_name] = f"{base_num * Constants.STORAGE_SCALE_PROD}{unit}"
                else:
                    # Fallback if parsing fails - use constants
                    storage_configs['test'][pvc_name] = Constants.DEFAULT_STORAGE_TEST
                    storage_configs['preprod'][pvc_name] = Constants.DEFAULT_STORAGE_PREPROD
                    storage_configs['prod'][pvc_name] = Constants.DEFAULT_STORAGE_PROD
            except Exception:
                # Fallback to default sizes using constants
                storage_configs['test'][pvc_name] = Constants.DEFAULT_STORAGE_TEST
                storage_configs['preprod'][pvc_name] = Constants.DEFAULT_STORAGE_PREPROD
                storage_configs['prod'][pvc_name] = Constants.DEFAULT_STORAGE_PROD
        
        return storage_configs
    
    def _safe_get_pvc_name(self, doc: dict, fallback_name: str) -> str:
        """Safely extract PVC name from document"""
        try:
            metadata = doc.get('metadata', {})
            return metadata.get('name', fallback_name)
        except (AttributeError, TypeError):
            return fallback_name
    
    def _safe_get_storage_size(self, doc: dict) -> str:
        """Safely extract storage size from PVC document with fallback chain"""
        try:
            # Try to get storage size through nested dict access
            spec = doc.get('spec')
            if not spec:
                return Constants.DEFAULT_STORAGE_TEST
                
            resources = spec.get('resources')
            if not resources:
                return Constants.DEFAULT_STORAGE_TEST
                
            requests = resources.get('requests')
            if not requests:
                return Constants.DEFAULT_STORAGE_TEST
                
            storage = requests.get('storage')
            return storage if storage else Constants.DEFAULT_STORAGE_TEST
            
        except (AttributeError, TypeError, KeyError):
            return Constants.DEFAULT_STORAGE_TEST

    def _generate_kustomizations(self, namespace: NamespaceConfig) -> None:
        """Generate Kustomization files for all environments"""
        
        # Generate base kustomization
        self._generate_base_kustomization(namespace)
        
        # Generate environment overlays
        self._generate_environment_overlays(namespace)
    
    def _generate_base_kustomization(self, namespace: NamespaceConfig) -> None:
        """Generate base kustomization file for the base environment"""
        base_kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'resources': [f"{rt}/" for rt in namespace.resources.keys()],
            'namespace': f"{namespace.name}-{self.base_env}",
            'namePrefix': f'{self.base_env}-',
            'commonLabels': {
                'environment': self.base_env,
                'app.kubernetes.io/managed-by': 'argocd',
                'app.kubernetes.io/part-of': namespace.name
            }
        }
        
        base_file = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / Constants.ENVIRONMENTS_DIR / self.base_env / "kustomization.yaml"
        
        # Use safe file writing
        content = yaml.dump(base_kustomization, default_flow_style=False, sort_keys=False)
        self._safe_write_file(base_file, content, "base Kustomization")
    
    def _generate_environment_overlays(self, namespace: NamespaceConfig) -> None:
        """Generate environment overlay kustomizations"""
        # Dynamically detect storage requirements
        dynamic_storage = self._detect_pvc_storage_requirements(namespace)
        
        # Generate overlays for non-base environments
        overlay_envs = [env for env in self.environments if env != self.base_env]
        
        for env in overlay_envs:
            overlay_config = self._create_overlay_config(namespace, env, dynamic_storage)
            self._generate_single_overlay(namespace, env, overlay_config)
    
    def _create_overlay_config(self, namespace: NamespaceConfig, env: str, dynamic_storage: dict) -> dict:
        """Create configuration for a single environment overlay"""
        # Get replica count for environment (index-based mapping)
        replica_mapping = [
            Constants.DEFAULT_REPLICAS_TEST,
            Constants.DEFAULT_REPLICAS_PREPROD, 
            Constants.DEFAULT_REPLICAS_PROD
        ]
        env_index = self.environments.index(env) - 1  # -1 because base env is excluded
        replicas = replica_mapping[min(env_index, len(replica_mapping) - 1)]
        
        return {
            'namespace': f"{namespace.name}-{env}",
            'namePrefix': f'{env}-',
            'replicas': replicas,
            'storage_patches': dynamic_storage.get(env, {})
        }
    
    def _generate_single_overlay(self, namespace: NamespaceConfig, env: str, config: dict) -> None:
        """Generate a single environment overlay kustomization"""
        overlay_kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'resources': [f"../{self.base_env}/{rt}/" for rt in namespace.resources.keys()],
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
        patches = self._create_environment_patches(namespace, env, config)
        
        if patches:
            overlay_kustomization['patches'] = patches
        
        overlay_file = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / Constants.ENVIRONMENTS_DIR / env / "kustomization.yaml"
        
        # Use safe file writing
        content = yaml.dump(overlay_kustomization, default_flow_style=False, sort_keys=False)
        self._safe_write_file(overlay_file, content, f"{env} environment Kustomization")
    
    def _create_environment_patches(self, namespace: NamespaceConfig, env: str, config: dict) -> list:
        """Create patches for environment-specific configurations"""
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
        
        return patches
    
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
        
        readme_file = Path(Constants.BASE_NAMESPACE_DIR) / namespace.name / "README.md"
        
        # Use safe file writing
        self._safe_write_file(readme_file, readme_content, "namespace README")
    
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
        
        # Use safe file writing for root README
        root_readme_path = Path('README.md')
        self._safe_write_file(root_readme_path, readme_content, "root README")

def load_config(config_path='config.yaml', validate_env=True):
    """Simplified configuration loading with environment variable support"""
    import os
    
    if not os.path.exists(config_path):
        raise ConfigurationError(f"Configuration file not found: {config_path}")
    
    # Validate config file size
    config_size = os.path.getsize(config_path)
    max_config_size = Constants.MAX_CONFIG_SIZE_MB * 1024 * 1024
    if config_size > max_config_size:
        raise FileSizeError(f"Configuration file exceeds size limit ({Constants.MAX_CONFIG_SIZE_MB}MB): {config_size / (1024*1024):.1f}MB")
    
    try:
        with open(config_path, 'r') as f:
            content = f.read()
            
            # Validate config content for security
            for pattern in Constants.DANGEROUS_PATTERNS:
                if pattern in content.lower():
                    raise SecurityError(f"Potentially dangerous pattern '{pattern}' found in config file")
            
            config = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in config file: {e}")
    
    # Simple environment variable override
    env_mappings = {
        'MINIO_ENDPOINT': ('minio', 'endpoint'),
        'MINIO_ACCESS_KEY': ('minio', 'access_key'),
        'MINIO_SECRET_KEY': ('minio', 'secret_key'),
        'MINIO_BUCKET': ('minio', 'bucket'),
        'GIT_REPOSITORY': ('git', 'repository')
    }
    
    # Apply environment overrides
    env_used = []
    for env_var, (section, key) in env_mappings.items():
        value = os.getenv(env_var)
        if value:
            if section not in config:
                config[section] = {}
            config[section][key] = value
            env_used.append(env_var)
    
    if env_used:
        print(f"🔐 Using environment variables: {', '.join(env_used)}")
    
    # Validate required fields
    required_fields = [
        ('minio.endpoint', config.get('minio', {}).get('endpoint')),
        ('minio.access_key', config.get('minio', {}).get('access_key')),
        ('minio.secret_key', config.get('minio', {}).get('secret_key')),
        ('minio.bucket', config.get('minio', {}).get('bucket')),
        ('git.repository', config.get('git', {}).get('repository')),
    ]
    
    for field_name, value in required_fields:
        if not value:
            raise ConfigurationError(f"Missing required configuration: {field_name}")
    
    # Validate cluster configuration
    if 'clusters' not in config or 'default' not in config['clusters']:
        raise ConfigurationError("Missing clusters.default configuration")
    
    # Extract environments from config (with fallback to default)
    environments = config.get('environments', Constants.DEFAULT_ENVIRONMENTS)
    for env in environments:
        if env not in config['clusters']['default']:
            raise ConfigurationError(f"Missing cluster mapping for: {env}")
    
    # Extract clean configurations
    minio_config = {
        'endpoint': config['minio']['endpoint'],
        'access_key': config['minio']['access_key'],
        'secret_key': config['minio']['secret_key'],
        'bucket': config['minio']['bucket'],
        'prefix': config['minio'].get('prefix', ''),
        'secure': config['minio'].get('secure', False)
    }
    
    return minio_config, config['clusters'], config['git']['repository'], config

# Note: _validate_environment_config function removed - validation now integrated into load_config

def create_backup(backup_name=None):
    """Create backup of existing namespaces directory"""
    import shutil
    from datetime import datetime
    
    namespaces_dir = Path(Constants.BASE_NAMESPACE_DIR)
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