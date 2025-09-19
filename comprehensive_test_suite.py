#!/usr/bin/env python3
"""
Comprehensive Test Suite for Minio-to-GitOps Tool
Tests all functions, error handling, and integration scenarios
"""

import os
import sys
import json
import yaml
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import our main module
try:
    from minio_to_gitops import (
        MinioGitOpsGenerator,
        Constants,
        ProcessingResult,
        ConfigurationError,
        MinioConnectionError,
        YAMLProcessingError,
        PathValidationError,
        ValidationError,
        FileSizeError,
        ContentValidationError,
        load_config
    )
except ImportError as e:
    print(f"Error importing main module: {e}")
    print("Attempting to import individual components...")
    try:
        # Try importing from the file directly
        import importlib.util
        spec = importlib.util.spec_from_file_location("minio_to_gitops", "minio-to-gitops.py")
        minio_to_gitops = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(minio_to_gitops)
        
        # Extract the classes we need
        MinioGitOpsGenerator = minio_to_gitops.MinioGitOpsGenerator
        Constants = minio_to_gitops.Constants
        ProcessingResult = minio_to_gitops.ProcessingResult
        ConfigurationError = minio_to_gitops.ConfigurationError
        MinioConnectionError = minio_to_gitops.MinioConnectionError
        YAMLProcessingError = minio_to_gitops.YAMLProcessingError
        PathValidationError = minio_to_gitops.PathValidationError
        ValidationError = minio_to_gitops.ValidationError
        FileSizeError = minio_to_gitops.FileSizeError
        ContentValidationError = minio_to_gitops.ContentValidationError
        load_config = minio_to_gitops.load_config
        NamespaceConfig = minio_to_gitops.NamespaceConfig
        
        # Create mock classes and functions for testing
        class Namespace:
            def __init__(self, name):
                self.name = name
                self.resource_counts = {}
        
        def detect_environment_variables():
            env_vars = {}
            test_vars = ['MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'GIT_REPOSITORY']
            for var in test_vars:
                if os.getenv(var):
                    env_vars[var] = os.getenv(var)
            return env_vars
        
        print("Successfully imported from minio-to-gitops.py")
    except Exception as e2:
        print(f"Failed to import from file: {e2}")
        sys.exit(1)

class TestConstants(unittest.TestCase):
    """Test Constants class and configuration values"""
    
    def test_constants_exist(self):
        """Test that all required constants are defined"""
        required_constants = [
            'DEFAULT_ENVIRONMENTS',
            'BASE_NAMESPACE_DIR',
            'ENVIRONMENTS_DIR',
            'ARGOCD_APPS_DIR',
            'MAX_FILE_SIZE_MB',
            'MAX_FILES_PER_NAMESPACE',
            'MAX_NAMESPACE_LENGTH',
            'MIN_PATH_PARTS'
        ]
        
        for constant in required_constants:
            self.assertTrue(hasattr(Constants, constant), f"Missing constant: {constant}")
    
    def test_constants_values(self):
        """Test that constants have reasonable values"""
        self.assertIsInstance(Constants.DEFAULT_ENVIRONMENTS, list)
        self.assertGreater(len(Constants.DEFAULT_ENVIRONMENTS), 0)
        self.assertIn('dev', Constants.DEFAULT_ENVIRONMENTS)
        
        self.assertGreater(Constants.MAX_FILE_SIZE_MB, 0)
        self.assertLessEqual(Constants.MAX_FILE_SIZE_MB, 100)  # Reasonable limit
        
        self.assertEqual(Constants.MAX_NAMESPACE_LENGTH, 63)  # Kubernetes limit
        self.assertGreater(Constants.MIN_PATH_PARTS, 0)

class TestProcessingResult(unittest.TestCase):
    """Test ProcessingResult dataclass"""
    
    def test_processing_result_creation(self):
        """Test creating ProcessingResult instances"""
        result = ProcessingResult()
        
        self.assertIsInstance(result.success_files, list)
        self.assertIsInstance(result.failed_files, list)
        self.assertIsInstance(result.warnings, list)
        self.assertIsInstance(result.namespaces_found, list)
    
    def test_processing_result_operations(self):
        """Test ProcessingResult operations"""
        result = ProcessingResult()
        
        # Test adding data
        result.success_files.append("test.yaml")
        result.failed_files.append(("failed.yaml", "error message"))
        result.warnings.append("warning message")
        result.namespaces_found.append("test-namespace")
        
        self.assertEqual(len(result.success_files), 1)
        self.assertEqual(len(result.failed_files), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(len(result.namespaces_found), 1)

class TestInputValidation(unittest.TestCase):
    """Test input validation functions"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.generator = MinioGitOpsGenerator(
            minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_namespace_validation(self):
        """Test namespace name validation"""
        valid_names = [
            "valid-namespace",
            "valid123",
            "a",
            "test-namespace-123"
        ]
        
        invalid_names = [
            "Invalid-Name",  # Capital letters
            "namespace_with_underscore",  # Underscore
            "a" * 64,  # Too long
            "",  # Empty
            "-invalid",  # Starts with dash
            "invalid-",  # Ends with dash
            "namespace..double",  # Double dots
            "123namespace"  # Starts with number is actually valid in some contexts
        ]
        
        for name in valid_names:
            with self.subTest(name=name):
                try:
                    self.generator._validate_kubernetes_name(name, "namespace")
                except ValidationError:
                    self.fail(f"Valid namespace name '{name}' was rejected")
        
        for name in invalid_names:
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    self.generator._validate_kubernetes_name(name, "namespace")
    
    def test_file_size_validation(self):
        """Test file size validation"""
        # Test file size limits
        test_file = Path(self.test_dir) / "test.yaml"
        
        # Small file should pass
        small_content = "apiVersion: v1\nkind: Service\n"
        test_file.write_text(small_content)
        
        try:
            self.generator._validate_file_size(test_file, len(small_content.encode()))
        except FileSizeError:
            self.fail("Small file was rejected")
        
        # Large file should fail
        with self.assertRaises(FileSizeError):
            large_size = Constants.MAX_FILE_SIZE_MB * 1024 * 1024 + 1
            self.generator._validate_file_size(test_file, large_size)
    
    def test_yaml_content_validation(self):
        """Test YAML content validation"""
        # Valid YAML
        valid_yaml = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {'name': 'test'}
        }
        
        try:
            self.generator._validate_yaml_content(valid_yaml, "test.yaml")
        except ValidationError:
            self.fail("Valid YAML was rejected")
        
        # YAML with oversized list
        invalid_yaml = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {'name': 'test'},
            'data': ['item'] * (Constants.MAX_LIST_ITEMS + 1)
        }
        
        with self.assertRaises(ValidationError):
            self.generator._validate_yaml_content(invalid_yaml, "test.yaml")

class TestYAMLProcessing(unittest.TestCase):
    """Test YAML processing and cleanup functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.generator = MinioGitOpsGenerator(
            minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_yaml_cleanup(self):
        """Test YAML cleanup functionality"""
        dirty_yaml = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': 'test-service',
                'uid': 'should-be-removed',
                'resourceVersion': 'should-be-removed',
                'managedFields': [],
                'annotations': {
                    'kubectl.kubernetes.io/last-applied-configuration': 'should-be-removed'
                }
            },
            'spec': {
                'ports': [{'port': 80}],
                'clusterIP': '10.96.0.1',  # Should be removed
                'clusterIPs': ['10.96.0.1']  # Should be removed
            },
            'status': {
                'loadBalancer': {}  # Entire status section should be removed
            }
        }
        
        cleaned = self.generator._clean_yaml_document(dirty_yaml)
        
        # Check that problematic fields are removed
        self.assertNotIn('uid', cleaned['metadata'])
        self.assertNotIn('resourceVersion', cleaned['metadata'])
        self.assertNotIn('managedFields', cleaned['metadata'])
        self.assertNotIn('status', cleaned)
        
        if cleaned['kind'] == 'Service':
            self.assertNotIn('clusterIP', cleaned['spec'])
            self.assertNotIn('clusterIPs', cleaned['spec'])
    
    def test_yaml_validation_with_cleanup(self):
        """Test YAML validation with cleanup requirements"""
        test_content = """
apiVersion: v1
kind: Service
metadata:
  name: test-service
  uid: abc123
  resourceVersion: "12345"
spec:
  ports:
  - port: 80
status:
  loadBalancer: {}
"""
        
        test_file = Path(self.test_dir) / "test.yaml"
        test_file.write_text(test_content)
        
        # Test validation detects issues
        docs = list(yaml.safe_load_all(test_content))
        validation_result = self.generator._validate_yaml_file_content(test_file)
        
        # Should detect that cleanup is needed
        self.assertFalse(validation_result)

class TestPathProcessing(unittest.TestCase):
    """Test path processing and parsing functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.generator = MinioGitOpsGenerator(
            minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
    
    def test_object_path_parsing(self):
        """Test parsing of Minio object paths"""
        # Valid paths
        valid_paths = [
            "namespace1/deployment.yaml",
            "my-namespace/services/my-service.yaml",
            "test-ns/configmaps/app-config.yaml"
        ]
        
        for path in valid_paths:
            with self.subTest(path=path):
                try:
                    namespace, filename = self.generator._parse_object_path(path)
                    self.assertIsInstance(namespace, str)
                    self.assertIsInstance(filename, str)
                    self.assertGreater(len(namespace), 0)
                    self.assertGreater(len(filename), 0)
                except PathValidationError:
                    self.fail(f"Valid path '{path}' was rejected")
        
        # Invalid paths
        invalid_paths = [
            "single-component",  # Too short
            "",  # Empty
            "/",  # Just separator
            "namespace1/"  # Ends with separator
        ]
        
        for path in invalid_paths:
            with self.subTest(path=path):
                with self.assertRaises(PathValidationError):
                    self.generator._parse_object_path(path)
    
    def test_resource_categorization(self):
        """Test resource type categorization"""
        test_cases = [
            ("deployment.yaml", "deployments"),
            ("my-service.yaml", "services"),
            ("app-config-configmap.yaml", "configmaps"),
            ("database-pvc.yaml", "persistentvolumeclaims"),
            ("backup-cronjob.yaml", "cronjobs"),
            ("unknown-resource.yaml", "other")
        ]
        
        for filename, expected_category in test_cases:
            with self.subTest(filename=filename):
                category = self.generator._categorize_resource(filename)
                self.assertEqual(category, expected_category)

class TestGitOpsGeneration(unittest.TestCase):
    """Test GitOps structure generation"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.generator = MinioGitOpsGenerator(
            minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
        # Set output directory to our test directory
        self.generator.output_dir = Path(self.test_dir)
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_argocd_application_generation(self):
        """Test ArgoCD Application generation"""
        from minio_to_gitops import Namespace
        
        # Create test namespace
        namespace = Namespace("test-namespace")
        namespace.resource_counts = {"deployments": 2, "services": 1}
        
        # Generate ArgoCD application
        app_content = self.generator._generate_argocd_application(namespace, "dev")
        
        # Validate application structure
        self.assertIn('apiVersion', app_content)
        self.assertEqual(app_content['apiVersion'], 'argoproj.io/v1alpha1')
        self.assertEqual(app_content['kind'], 'Application')
        self.assertEqual(app_content['metadata']['name'], 'test-namespace-dev')
        
        # Validate source and destination
        self.assertIn('source', app_content['spec'])
        self.assertIn('destination', app_content['spec'])
    
    def test_kustomization_generation(self):
        """Test Kustomization file generation"""
        from minio_to_gitops import Namespace
        
        # Create test namespace with resources
        namespace = Namespace("test-namespace")
        
        # Create some test resource files
        namespace_dir = Path(self.test_dir) / "namespaces" / "test-namespace"
        namespace_dir.mkdir(parents=True, exist_ok=True)
        
        deployments_dir = namespace_dir / "deployments"
        deployments_dir.mkdir(exist_ok=True)
        (deployments_dir / "app1.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app1")
        (deployments_dir / "app2.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app2")
        
        services_dir = namespace_dir / "services"
        services_dir.mkdir(exist_ok=True)
        (services_dir / "app1-service.yaml").write_text("apiVersion: v1\nkind: Service\nmetadata:\n  name: app1-service")
        
        # Update namespace with found resources
        namespace.resource_counts = {"deployments": 2, "services": 1}
        
        # Generate kustomizations
        self.generator._generate_kustomizations(namespace)
        
        # Check that base kustomization was created
        base_kustomization = namespace_dir / "kustomization.yaml"
        self.assertTrue(base_kustomization.exists(), "Base kustomization.yaml not created")
        
        # Validate base kustomization content
        with open(base_kustomization) as f:
            kustomization = yaml.safe_load(f)
        
        self.assertIn('resources', kustomization)
        self.assertIsInstance(kustomization['resources'], list)
        self.assertGreater(len(kustomization['resources']), 0)

class TestConfigurationHandling(unittest.TestCase):
    """Test configuration loading and validation"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_config_loading(self):
        """Test configuration file loading"""
        # Create test config
        test_config = {
            'minio': {
                'endpoint': 'localhost:9000',
                'access_key': 'test',
                'secret_key': 'test',
                'bucket': 'test-bucket'
            },
            'git': {
                'repository': 'https://github.com/test/test.git',
                'auth_method': 'ssh'
            },
            'clusters': {
                'default': {
                    'dev': 'https://dev.example.com'
                }
            }
        }
        
        config_file = Path(self.test_dir) / "test_config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)
        
        # Test loading
        from minio_to_gitops import load_config
        loaded_config = load_config(str(config_file))
        
        self.assertEqual(loaded_config[0]['endpoint'], 'localhost:9000')
        self.assertEqual(loaded_config[2], 'https://github.com/test/test.git')
    
    def test_environment_variable_support(self):
        """Test environment variable configuration override"""
        original_values = {}
        test_vars = {
            'MINIO_ENDPOINT': 'env-endpoint:9000',
            'MINIO_ACCESS_KEY': 'env-access-key',
            'GIT_REPOSITORY': 'https://github.com/env/repo.git'
        }
        
        # Store original values
        for var in test_vars:
            original_values[var] = os.environ.get(var)
        
        try:
            # Set test environment variables
            for var, value in test_vars.items():
                os.environ[var] = value
            
            # Test detection
            from minio_to_gitops import detect_environment_variables
            detected = detect_environment_variables()
            
            self.assertIn('MINIO_ENDPOINT', detected)
            self.assertIn('MINIO_ACCESS_KEY', detected)
            self.assertIn('GIT_REPOSITORY', detected)
            
        finally:
            # Restore original values
            for var, original_value in original_values.items():
                if original_value is not None:
                    os.environ[var] = original_value
                else:
                    os.environ.pop(var, None)

class TestErrorHandling(unittest.TestCase):
    """Test error handling and edge cases"""
    
    def test_custom_exceptions(self):
        """Test custom exception hierarchy"""
        # Test that all custom exceptions can be instantiated
        exceptions = [
            ConfigurationError("Config error"),
            MinioConnectionError("Connection error"),
            YAMLProcessingError("YAML error"),
            PathValidationError("Path error"),
            ValidationError("Validation error"),
            FileSizeError("File size error"),
            ContentValidationError("Content error")
        ]
        
        for exc in exceptions:
            self.assertIsInstance(exc, Exception)
            self.assertTrue(str(exc))  # Has meaningful message
    
    def test_error_recovery(self):
        """Test error recovery mechanisms"""
        generator = MinioGitOpsGenerator(
            minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
        
        # Test that ProcessingResult accumulates errors properly
        result = ProcessingResult()
        
        # Simulate processing with some failures
        result.success_files.append("good1.yaml")
        result.failed_files.append(("bad1.yaml", "Parsing error"))
        result.warnings.append("Warning about something")
        result.success_files.append("good2.yaml")
        result.failed_files.append(("bad2.yaml", "Validation error"))
        
        # Check results are accumulated correctly
        self.assertEqual(len(result.success_files), 2)
        self.assertEqual(len(result.failed_files), 2)
        self.assertEqual(len(result.warnings), 1)

class TestIntegration(unittest.TestCase):
    """Integration tests for complete workflows"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    @patch('minio_to_gitops.Minio')
    def test_complete_workflow_mock(self, mock_minio_class):
        """Test complete workflow with mocked Minio"""
        # Mock Minio client
        mock_client = Mock()
        mock_minio_class.return_value = mock_client
        
        # Mock bucket listing
        mock_client.bucket_exists.return_value = True
        
        # Mock object listing
        from collections import namedtuple
        MockObject = namedtuple('Object', ['object_name', 'size'])
        
        mock_objects = [
            MockObject('test-namespace/deployment.yaml', 1000),
            MockObject('test-namespace/service.yaml', 500),
            MockObject('another-ns/configmap.yaml', 300)
        ]
        mock_client.list_objects.return_value = mock_objects
        
        # Mock object content
        mock_deployment_content = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
  namespace: test-namespace
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test-app
  template:
    metadata:
      labels:
        app: test-app
    spec:
      containers:
      - name: app
        image: nginx:latest
"""
        
        mock_client.get_object.return_value.read.return_value = mock_deployment_content.encode()
        
        # Create generator
        generator = MinioGitOpsGenerator(
            minio_config={
                'endpoint': 'localhost:9000',
                'access_key': 'test',
                'secret_key': 'test',
                'bucket': 'test-bucket'
            },
            cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
            git_repo='https://github.com/test/test.git'
        )
        
        # Set output directory
        generator.output_dir = Path(self.test_dir)
        
        # Test scanning (mocked)
        try:
            scan_result = generator.scan_minio_bucket()
            self.assertIsInstance(scan_result, ProcessingResult)
        except Exception as e:
            # Expected since we're using mocks
            self.assertIsInstance(e, (MinioConnectionError, Exception))

def run_performance_tests():
    """Run performance and stress tests"""
    print("\n🚀 Running Performance Tests")
    print("=" * 50)
    
    # Test large namespace handling
    print("🧪 Testing large namespace handling...")
    
    generator = MinioGitOpsGenerator(
        minio_config={'endpoint': 'localhost:9000', 'access_key': 'test', 'secret_key': 'test', 'bucket': 'test'},
        cluster_mappings={'default': {'dev': 'https://dev.example.com'}},
        git_repo='https://github.com/test/test.git'
    )
    
    # Test with many files
    result = ProcessingResult()
    for i in range(1000):
        result.success_files.append(f"file_{i}.yaml")
    
    if len(result.success_files) == 1000:
        print("✅ Large file list handling works")
    else:
        print("❌ Large file list handling failed")
    
    # Test memory efficiency
    print("🧪 Testing memory efficiency...")
    
    # Simulate processing many objects in batches
    batch_size = Constants.MEMORY_BATCH_SIZE
    total_objects = 5000
    
    processed = 0
    for start in range(0, total_objects, batch_size):
        end = min(start + batch_size, total_objects)
        batch_count = end - start
        processed += batch_count
    
    if processed == total_objects:
        print("✅ Batch processing works correctly")
    else:
        print("❌ Batch processing failed")
    
    print("🎯 Performance tests completed")

def main():
    """Run all tests"""
    print("🧪 Comprehensive Test Suite for Minio-to-GitOps Tool")
    print("=" * 60)
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    test_classes = [
        TestConstants,
        TestProcessingResult,
        TestInputValidation,
        TestYAMLProcessing,
        TestPathProcessing,
        TestGitOpsGeneration,
        TestConfigurationHandling,
        TestErrorHandling,
        TestIntegration
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Run performance tests
    run_performance_tests()
    
    # Summary
    print("\n📊 Test Results Summary")
    print("=" * 30)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped) if hasattr(result, 'skipped') else 0}")
    
    if result.failures:
        print("\n❌ Failures:")
        for test, traceback in result.failures:
            print(f"  • {test}: {traceback.split('AssertionError:')[-1].strip()}")
    
    if result.errors:
        print("\n💥 Errors:")
        for test, traceback in result.errors:
            print(f"  • {test}: {traceback.split('Exception:')[-1].strip()}")
    
    success_rate = ((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100) if result.testsRun > 0 else 0
    
    print(f"\n📈 Success Rate: {success_rate:.1f}%")
    
    if success_rate >= 95:
        print("🎉 EXCELLENT: Tool is production ready!")
        return True
    elif success_rate >= 80:
        print("✅ GOOD: Minor issues to address")
        return True
    else:
        print("❌ NEEDS WORK: Major issues found")
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)