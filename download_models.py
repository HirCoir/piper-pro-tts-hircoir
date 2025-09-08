import os
import json
import subprocess
import requests
from urllib.parse import urljoin
import base64
import hashlib

def calculate_sha256(file_path):
    """Calculate SHA256 hash of a file"""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest().upper()
    except Exception as e:
        print(f"Error calculating SHA256 for {file_path}: {e}")
        return None

def verify_file_integrity(file_path, expected_sha256):
    """Verify file integrity using SHA256 hash"""
    if not expected_sha256:
        return True  # No hash to verify against
    
    actual_sha256 = calculate_sha256(file_path)
    if actual_sha256 is None:
        return False
    
    return actual_sha256 == expected_sha256.upper()

def get_expected_sha256(models_dir, model_id):
    """Get expected SHA256 from model's JSON file"""
    json_path = os.path.join(models_dir, f"{model_id}.onnx.json")
    if not os.path.exists(json_path):
        return None
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('modelcard', {}).get('sha256')
    except Exception as e:
        print(f"Error reading SHA256 from {json_path}: {e}")
        return None

def should_download_file(file_path, expected_sha256):
    """Determine if file should be downloaded based on existence and SHA256"""
    if not os.path.exists(file_path):
        print(f"File {os.path.basename(file_path)} does not exist, downloading...")
        return True
    
    if not expected_sha256:
        print(f"No SHA256 hash available for {os.path.basename(file_path)}, skipping verification")
        return False
    
    if verify_file_integrity(file_path, expected_sha256):
        print(f"File {os.path.basename(file_path)} SHA256 verified, skipping download")
        return False
    else:
        print(f"File {os.path.basename(file_path)} SHA256 mismatch, re-downloading...")
        return True

def download_from_huggingface():
    """Download models from Hugging Face"""
    print("Iniciando descarga desde Hugging Face...")
    
    # Load the models JSON
    if not os.path.exists('modelos.json'):
        print("Archivo modelos.json no encontrado, omitiendo descarga de HF")
        return
        
    with open('modelos.json', 'r') as f:
        models = json.load(f)

    # Define the Hugging Face repo and token
    repo = os.getenv('REPO_HUGGINGFACE')
    token = os.getenv('TOKEN_HUGGINGFACE')
    
    if not repo:
        print("REPO_HUGGINGFACE no definido, omitiendo descarga de HF")
        return

    # Set the Hugging Face token
    if token:
        os.makedirs('/root/.cache/huggingface', exist_ok=True)
        with open('/root/.cache/huggingface/token', 'w') as f:
            f.write(token)

    # Create models directory
    models_dir = os.getenv('MODELS_DIR', '/home/app/models')
    os.makedirs(models_dir, exist_ok=True)

    # Download the models
    for model in models['models']:
        model_id = model['id']
        onnx_file = f"{model_id}.onnx"
        onnx_json_file = f"{model_id}.onnx.json"
        
        onnx_path = os.path.join(models_dir, onnx_file)
        json_path = os.path.join(models_dir, onnx_json_file)
        
        # Get expected SHA256 from existing JSON file
        expected_sha256 = get_expected_sha256(models_dir, model_id)
        
        # Check if ONNX file needs to be downloaded
        download_onnx = should_download_file(onnx_path, expected_sha256)
        download_json = not os.path.exists(json_path)

        # Download files if needed
        try:
            if download_json:
                print(f"Downloading {onnx_json_file}...")
                subprocess.run(['huggingface-cli', 'download', repo, onnx_json_file, '--local-dir', models_dir], check=True)
                
            if download_onnx:
                print(f"Downloading {onnx_file}...")
                subprocess.run(['huggingface-cli', 'download', repo, onnx_file, '--local-dir', models_dir], check=True)
                
                # Verify downloaded file if we have expected hash
                if expected_sha256 and os.path.exists(onnx_path):
                    if verify_file_integrity(onnx_path, expected_sha256):
                        print(f"✅ {onnx_file} SHA256 verification passed")
                    else:
                        print(f"❌ {onnx_file} SHA256 verification failed!")
                        
            if download_onnx or download_json:
                print(f"Downloaded files for model {model_id}")
            else:
                print(f"All files for model {model_id} are up to date")
                
        except subprocess.CalledProcessError:
            print(f"Failed to download files for model {model_id}, skipping...")

def download_from_webdav():
    """Download models from WebDAV server"""
    webdav_url = os.getenv('WEBDAV_URL')
    webdav_user = os.getenv('WEBDAV_USER')
    webdav_password = os.getenv('WEBDAV_PASSWORD')
    
    if not all([webdav_url, webdav_user, webdav_password]):
        print("Variables WebDAV no definidas, omitiendo descarga WebDAV")
        return
        
    print("Iniciando descarga desde WebDAV...")
    
    models_dir = os.getenv('MODELS_DIR', '/home/app/models')
    os.makedirs(models_dir, exist_ok=True)
    
    # Create authentication header
    auth_string = f"{webdav_user}:{webdav_password}"
    auth_bytes = auth_string.encode('ascii')
    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
    headers = {
        'Authorization': f'Basic {auth_b64}',
        'User-Agent': 'Piper-TTS-Downloader/1.0'
    }
    
    try:
        # List files in WebDAV directory
        response = requests.request('PROPFIND', webdav_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Parse WebDAV response to find .onnx and .onnx.json files
        content = response.text
        print(f"WebDAV response length: {len(content)} chars")
        
        files_to_download = []
        
        # Try multiple parsing methods for different WebDAV server responses
        import re
        
        # Method 1: Look for href attributes in XML
        href_pattern = r'<(?:d:)?href[^>]*>([^<]+)</(?:d:)?href>'
        href_matches = re.findall(href_pattern, content, re.IGNORECASE)
        
        for href in href_matches:
            filename = href.split('/')[-1]
            if filename.endswith(('.onnx', '.onnx.json')):
                files_to_download.append(filename)
                print(f"Found file via href: {filename}")
        
        # Method 2: Look for displayname tags
        displayname_pattern = r'<(?:d:)?displayname[^>]*>([^<]+)</(?:d:)?displayname>'
        displayname_matches = re.findall(displayname_pattern, content, re.IGNORECASE)
        
        for displayname in displayname_matches:
            if displayname.endswith(('.onnx', '.onnx.json')):
                if displayname not in files_to_download:
                    files_to_download.append(displayname)
                    print(f"Found file via displayname: {displayname}")
        
        # Method 3: Simple text search as fallback
        if not files_to_download:
            print("Trying fallback text search...")
            for line in content.split('\n'):
                if '.onnx' in line:
                    # Look for .onnx files in the line
                    onnx_matches = re.findall(r'([\w\-\.]+\.onnx(?:\.json)?)', line)
                    for match in onnx_matches:
                        if match not in files_to_download:
                            files_to_download.append(match)
                            print(f"Found file via text search: {match}")
        
        print(f"Total files found: {len(files_to_download)}")
        if not files_to_download:
            print("No .onnx files found. WebDAV response preview:")
            print(content[:500] + "..." if len(content) > 500 else content)
        
        # Download each file
        for filename in files_to_download:
            # Ensure proper URL joining
            if webdav_url.endswith('/'):
                file_url = webdav_url + filename
            else:
                file_url = webdav_url + '/' + filename
                
            local_path = os.path.join(models_dir, filename)
            
            # Check if file needs to be downloaded (with SHA256 verification for .onnx files)
            if filename.endswith('.onnx'):
                model_id = filename[:-5]  # Remove .onnx extension
                expected_sha256 = get_expected_sha256(models_dir, model_id)
                
                if not should_download_file(local_path, expected_sha256):
                    continue
            elif os.path.exists(local_path):
                print(f"Archivo {filename} ya existe, omitiendo...")
                continue
                
            try:
                print(f"Descargando {filename} desde {file_url}...")
                response = requests.get(file_url, headers=headers, timeout=300)
                response.raise_for_status()
                
                # Check if we got actual file content
                if len(response.content) == 0:
                    print(f"Advertencia: {filename} está vacío")
                    continue
                    
                with open(local_path, 'wb') as f:
                    f.write(response.content)
                    
                file_size = len(response.content)
                print(f"Descargado: {filename} ({file_size} bytes)")
                
                # Verify SHA256 for .onnx files
                if filename.endswith('.onnx'):
                    model_id = filename[:-5]
                    expected_sha256 = get_expected_sha256(models_dir, model_id)
                    if expected_sha256 and verify_file_integrity(local_path, expected_sha256):
                        print(f"✅ {filename} SHA256 verification passed")
                    elif expected_sha256:
                        print(f"❌ {filename} SHA256 verification failed!")
                
            except Exception as e:
                print(f"Error descargando {filename}: {e}")
                
    except Exception as e:
        print(f"Error conectando a WebDAV: {e}")

def download_from_github():
    """Download models from GitHub repository"""
    github_repo = os.getenv('GITHUB_REPO')  # Format: owner/repo or full URL
    github_path = os.getenv('GITHUB_PATH', '')  # Path within repo
    github_token = os.getenv('GITHUB_TOKEN')  # Optional
    
    if not github_repo:
        print("GITHUB_REPO no definido, omitiendo descarga de GitHub")
        return
    
    # Extract owner/repo from full URL if needed
    if github_repo.startswith('https://github.com/'):
        github_repo = github_repo.replace('https://github.com/', '')
    if github_repo.endswith('.git'):
        github_repo = github_repo[:-4]
        
    print(f"Iniciando descarga desde GitHub: {github_repo}")
    
    models_dir = os.getenv('MODELS_DIR', '/home/app/models')
    os.makedirs(models_dir, exist_ok=True)
    
    headers = {'User-Agent': 'Piper-TTS-Downloader/1.0'}
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    
    try:
        # Get repository contents
        api_url = f"https://api.github.com/repos/{github_repo}/contents/{github_path}"
        print(f"GitHub API URL: {api_url}")
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        contents = response.json()
        
        # Handle both single file and directory listing
        if isinstance(contents, dict):
            contents = [contents]
            
        for item in contents:
            if item['type'] == 'file' and item['name'].endswith(('.onnx', '.onnx.json')):
                filename = item['name']
                download_url = item['download_url']
                local_path = os.path.join(models_dir, filename)
                
                # Check if file needs to be downloaded (with SHA256 verification for .onnx files)
                if filename.endswith('.onnx'):
                    model_id = filename[:-5]  # Remove .onnx extension
                    expected_sha256 = get_expected_sha256(models_dir, model_id)
                    
                    if not should_download_file(local_path, expected_sha256):
                        continue
                elif os.path.exists(local_path):
                    print(f"Archivo {filename} ya existe, omitiendo...")
                    continue
                    
                try:
                    print(f"Descargando {filename}...")
                    response = requests.get(download_url, headers=headers, timeout=300)
                    response.raise_for_status()
                    
                    with open(local_path, 'wb') as f:
                        f.write(response.content)
                        
                    print(f"Descargado: {filename}")
                    
                    # Verify SHA256 for .onnx files
                    if filename.endswith('.onnx'):
                        model_id = filename[:-5]
                        expected_sha256 = get_expected_sha256(models_dir, model_id)
                        if expected_sha256 and verify_file_integrity(local_path, expected_sha256):
                            print(f"✅ {filename} SHA256 verification passed")
                        elif expected_sha256:
                            print(f"❌ {filename} SHA256 verification failed!")
                    
                except Exception as e:
                    print(f"Error descargando {filename}: {e}")
                    
    except Exception as e:
        print(f"Error conectando a GitHub: {e}")

def main():
    """Main download function"""
    print("=== Descargador de Modelos Piper TTS ===")
    
    # Check which download methods are configured
    methods = []
    if os.getenv('REPO_HUGGINGFACE'):
        methods.append("Hugging Face")
    if all([os.getenv('WEBDAV_URL'), os.getenv('WEBDAV_USER'), os.getenv('WEBDAV_PASSWORD')]):
        methods.append("WebDAV")
    if os.getenv('GITHUB_REPO'):
        methods.append("GitHub")
        
    if not methods:
        print("No hay métodos de descarga configurados.")
        print("Configure al menos una de estas variables:")
        print("- REPO_HUGGINGFACE (para Hugging Face)")
        print("- WEBDAV_URL, WEBDAV_USER, WEBDAV_PASSWORD (para WebDAV)")
        print("- GITHUB_REPO (para GitHub)")
        return
        
    print(f"Métodos configurados: {', '.join(methods)}")
    
    # Execute downloads
    if os.getenv('REPO_HUGGINGFACE'):
        download_from_huggingface()
        
    if all([os.getenv('WEBDAV_URL'), os.getenv('WEBDAV_USER'), os.getenv('WEBDAV_PASSWORD')]):
        download_from_webdav()
        
    if os.getenv('GITHUB_REPO'):
        download_from_github()
        
    print("=== Descarga completada ===")

if __name__ == "__main__":
    main()
