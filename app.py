import json
import logging
import os
import random
import re
import string
import subprocess
import time
import tempfile
import shutil
import base64
import concurrent.futures
from flask import Flask, request, jsonify, after_this_request, send_file, Response, render_template, session, redirect, url_for
import math
from werkzeug.middleware.proxy_fix import ProxyFix
import io
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Configuración del registro
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, template_folder='templates')
app.secret_key = os.urandom(24)

# Configura ProxyFix para confiar en las cabeceras de cualquier proxy (Cloudflare, nginx, etc.)
# Permite múltiples proxies en cadena y es compatible con Cloudflare Tunnel
app.wsgi_app = ProxyFix(
    app.wsgi_app, 
    x_for=10,      # Permite hasta 10 proxies en cadena (Cloudflare + otros)
    x_proto=10,    # Confía en X-Forwarded-Proto de múltiples proxies
    x_host=10,     # Confía en X-Forwarded-Host de múltiples proxies
    x_prefix=10,   # Confía en X-Forwarded-Prefix de múltiples proxies
    x_port=10      # Confía en X-Forwarded-Port de múltiples proxies
)

# Security configuration
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
import ipaddress

# Rate limiting storage
request_counts = {}
blocked_ips = {}
blocked_user_agents = set()

# Security settings
MAX_REQUESTS_PER_MINUTE = 10
MAX_REQUESTS_PER_HOUR = 100
BLOCK_DURATION_MINUTES = 30
MAX_TEXT_LENGTH = 5000

# Suspicious user agents patterns
SUSPICIOUS_USER_AGENTS = [
    'curl', 'wget', 'python-requests', 'python-urllib', 'postman', 'insomnia',
    'httpie', 'bot', 'crawler', 'spider', 'scraper', 'automated', 'test',
    'mozilla/4.0', 'mozilla/5.0 (compatible;)', 'java/', 'go-http-client',
    'okhttp', 'apache-httpclient', 'node-fetch', 'axios'
]

# Valid user agents (whitelist approach)
VALID_USER_AGENTS = [
    'mozilla/5.0 (windows nt', 'mozilla/5.0 (macintosh;', 'mozilla/5.0 (x11;',
    'mozilla/5.0 (iphone;', 'mozilla/5.0 (ipad;', 'mozilla/5.0 (android',
    'edge/', 'chrome/', 'firefox/', 'safari/', 'opera/'
]

# Define el directorio donde se guardan los archivos
file_folder = './'
temp_audio_folder = os.path.join(file_folder, 'temp_audio')
model_folder = os.path.join(file_folder, 'models')

# Detectar sistema operativo para usar el binario correcto de piper
if os.name == 'nt':  # Windows
    piper_binary_path = os.path.join(file_folder, 'piper', 'piper.exe')
else:  # Linux/Unix
    piper_binary_path = os.path.join(file_folder, 'piper', 'piper')

# Detectar la ubicación de ffmpeg
if os.name == 'nt':  # Windows
    ffmpeg_path = "ffmpeg"
    try:
        subprocess.run([ffmpeg_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        logging.warning("ffmpeg no encontrado en el PATH de Windows. Intentando con ruta local.")
        ffmpeg_path = os.path.join(file_folder, 'ffmpeg', 'ffmpeg.exe')
        if not os.path.exists(ffmpeg_path):
             logging.error(f"ffmpeg.exe no encontrado en el PATH ni en la ruta local: {ffmpeg_path}")
             # Si no se encuentra, se puede optar por salir o manejar de otra forma
             # raise FileNotFoundError(f"ffmpeg.exe not found at {ffmpeg_path}")
else:  # Linux/Unix
    ffmpeg_path = "/usr/bin/ffmpeg"
    if not os.path.exists(ffmpeg_path):
        try:
            ffmpeg_path = subprocess.check_output(["which", "ffmpeg"], text=True).strip()
            if not ffmpeg_path:
                logging.warning("ffmpeg no encontrado en el sistema. Intentando con nombre simple.")
                ffmpeg_path = "ffmpeg"
        except (subprocess.SubProcessError, FileNotFoundError):
            logging.warning("ffmpeg no encontrado en el sistema. Intentando con nombre simple.")
            ffmpeg_path = "ffmpeg"
    if not os.path.exists(ffmpeg_path):
        logging.error(f"ffmpeg no encontrado en /usr/bin/ffmpeg ni en el PATH. Por favor, instale ffmpeg.")
        # raise FileNotFoundError(f"ffmpeg not found at {ffmpeg_path}")


os.makedirs(temp_audio_folder, exist_ok=True)
os.makedirs(model_folder, exist_ok=True)

# Function to load models from individual .onnx.json files
def load_models():
    global model_configs, existing_models, model_id_to_filename_map
    model_configs = {}
    existing_models = []
    model_id_to_filename_map = {}  # Maps JSON ID to filename-based key
    
    try:
        # Scan models directory for .onnx.json files
        for filename in os.listdir(model_folder):
            if filename.endswith('.onnx.json'):
                model_filename_key = filename[:-10]  # Remove .onnx.json (e.g., "es_MX-lilith-9494")
                json_path = os.path.join(model_folder, filename)
                
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        model_data = json.load(f)
                    
                    # Get model info from modelcard section
                    modelcard = model_data.get('modelcard', {})
                    json_model_id = modelcard.get('id', model_filename_key)  # e.g., "es_MX-lilith"
                    
                    # Check if ONNX file exists
                    onnx_path = os.path.join(model_folder, f"{model_filename_key}.onnx")
                    if os.path.exists(onnx_path):
                        # Get model-specific replacements from modelcard, or use defaults
                        model_replacements = modelcard.get('replacements', [('\n', ' . '), ('*', ''), (')', ',')])
                        # Convert to tuples if they're lists
                        if model_replacements and isinstance(model_replacements[0], list):
                            model_replacements = [tuple(item) for item in model_replacements]
                        
                        model_config = {
                            "model_path_onnx": onnx_path,
                            "replacements": model_replacements,
                            "id": json_model_id,
                            "name": modelcard.get('name') or json_model_id,
                            "description": modelcard.get('description') or json_model_id,
                            "language": modelcard.get('language', 'Not available'),
                            "voiceprompt": modelcard.get('voiceprompt', 'Not available'),
                            "filename_key": model_filename_key,
                            "image": modelcard.get('image')  # Get base64 image from modelcard if present
                        }
                        
                        # Store model config using filename-based key
                        model_configs[model_filename_key] = model_config
                        existing_models.append(model_filename_key)
                        
                        # Create mapping from JSON ID to filename key (if they're different)
                        if json_model_id != model_filename_key:
                            model_id_to_filename_map[json_model_id] = model_filename_key
                            # Also store config using JSON ID for direct access
                            model_configs[json_model_id] = model_config
                            existing_models.append(json_model_id)
                        
                        logging.info(f"Loaded model: {model_filename_key} (ID: {json_model_id}) - {modelcard.get('name', model_filename_key)}")
                    
                except Exception as e:
                    logging.error(f"Error loading model {model_filename_key}: {e}")
    
    except Exception as e:
        logging.error(f"Error scanning models directory: {e}")
        model_configs = {}
        existing_models = []
        model_id_to_filename_map = {}

# Initial model loading
model_id_to_filename_map = {}  # Global mapping variable
load_models()

@app.route('/')
def index():
    # Prepare model data for the template
    model_options = []
    for model_key in existing_models:
        model_config = model_configs[model_key]
        model_options.append({
            "id": model_config["id"],
            "name": model_config["name"],
            "description": model_config["description"],
            "language": model_config["language"],
            "image": model_config.get("image", "")
        })
    
    return render_template('index.html', model_options=model_options)

@app.route('/favicon.ico')
def favicon():
    return send_file('templates/favicon.ico', mimetype='image/x-icon')

@app.route('/og_image')
def og_image():
    return send_file('templates/image.jpg', mimetype='image/jpeg')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Get users from environment variable
        users_env = os.environ.get('USERS', '')
        logging.info(f'Users environment variable: {repr(users_env)}')
        if not users_env:
            return render_template('login.html', error='No users configured')
        
        # Parse users from environment variable (format: 'user1,pass1|user2,pass2|...')
        users = {}
        for user_pass in users_env.split('|'):
            if ',' in user_pass:
                user, pwd = user_pass.split(',', 1)
                # Strip quotes and whitespace
                user = user.strip().strip('"\'')
                pwd = pwd.strip().strip('"\'')
                users[user] = pwd
        logging.info(f'Parsed users: {users}')
        logging.info(f'Submitted credentials: username={username}, password={password}')
        
        # Check credentials
        if username in users and users[username] == password:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Credenciales inválidas')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

# Load global replacements from JSON file
def load_global_replacements():
    """Load global text replacements from JSON file"""
    try:
        replacements_file = os.path.join(file_folder, 'global_replacements.json')
        if os.path.exists(replacements_file):
            with open(replacements_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [tuple(item) for item in data.get('global_replacements', [])]
        else:
            logging.warning(f"global_replacements.json not found at {replacements_file}. No replacements will be used.")
            return []
    except Exception as e:
        logging.error(f"Error loading global_replacements.json: {e}. No replacements will be used.")
        return []

global_replacements = load_global_replacements()

MAX_WORKERS = min(32, math.ceil((os.cpu_count() or 1) * 1.5))
logging.info(f"Initializing ThreadPoolExecutor with {MAX_WORKERS} workers.")
executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)

def random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def multiple_replace(text, replacements):
    """
    Apply text replacements with proper word boundary handling to avoid partial matches.
    Only replaces complete words/phrases separated by spaces, not partial matches.
    
    Args:
        text (str): Input text to process
        replacements (list): List of (find, replace) tuples
    
    Returns:
        str: Text with replacements applied
    """
    if not text or not replacements:
        return text
    
    logging.debug(f"[REPLACEMENTS] Starting text: '{text[:100]}{'...' if len(text) > 100 else ''}'")
    original_text = text
    
    for old, new in replacements:
        if not old:  # Skip empty find strings
            continue
            
        # Count occurrences before replacement for logging
        before_count = text.count(old)
        
        # Always use word boundaries for intelligent replacement
        # This ensures we only replace complete words/phrases, not partial matches
        
        # Special handling for abbreviations ending with period
        if old.endswith('.'):
            # For abbreviations like "Mr.", "Dr.", use exact match with word boundary before
            pattern = r'\b' + re.escape(old)
            text = re.sub(pattern, new, text, flags=re.IGNORECASE)
        elif ' ' in old:
            # For multi-word phrases like "1 día", "2 días", use exact phrase matching
            # This prevents "15 días" from being affected by "1" -> "uno" and "5" -> "cinco"
            pattern = r'\b' + re.escape(old) + r'\b'
            text = re.sub(pattern, new, text, flags=re.IGNORECASE)
        else:
            # For single words/numbers, use strict word boundaries
            # Avoid replacing numbers that are part of larger numbers or have commas/decimals
            if old.isdigit():
                # Don't replace if the number is part of a larger number, decimal, or comma-separated
                # BUT allow replacement when followed by period (enumeration context)
                pattern = r'\b' + re.escape(old) + r'(?![0-9,]|\.(?!\s))'
                text = re.sub(pattern, new, text, flags=re.IGNORECASE)
            else:
                # For non-numeric replacements, use standard word boundaries
                pattern = r'\b' + re.escape(old) + r'\b'
                text = re.sub(pattern, new, text, flags=re.IGNORECASE)
        
        # Count occurrences after replacement for logging
        after_count = text.count(old)
        replacements_made = before_count - after_count
        
        if replacements_made > 0:
            logging.debug(f"[REPLACEMENTS] '{old}' → '{new}' ({replacements_made} replacements)")
    
    if text != original_text:
        logging.debug(f"[REPLACEMENTS] Final text: '{text[:100]}{'...' if len(text) > 100 else ''}'")
    else:
        logging.debug(f"[REPLACEMENTS] No changes made to text")
    
    return text

def filter_code_blocks(text):
    return re.sub(r'```[^`\n]*\n.*?```', '', text, flags=re.DOTALL)

def process_line_breaks(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ''
    processed = []
    for i, line in enumerate(lines):
        # Si no es la última línea y no termina en puntuación, agregar coma (no punto)
        if i < len(lines) - 1 and not re.search(r'[.!?,:;]$', line):
            processed.append(line + ',')
        else:
            processed.append(line)
    processed_text = ' '.join(processed)
    # Refine punctuation placement
    processed_text = re.sub(r'(\))(?![.,;!?"\'])(?=\s|$)', r'\1,', processed_text) # Add comma after ')' if not followed by punctuation
    processed_text = re.sub(r'(\.)(\s*\.)+', r'\1', processed_text) # Collapse multiple periods
    processed_text = re.sub(r'(\s\.)', r'.', processed_text) # Remove space before period
    processed_text = re.sub(r'(\s,)', r',', processed_text) # Remove space before comma
    # Evitar secuencias problemáticas como ",."
    processed_text = re.sub(r',\s*\.', ',', processed_text) # Remove period after comma
    # Reemplazar puntos después de números (tanto en texto como dígitos) con comas para evitar segmentación
    # Incluir números hasta 30 y algunos números mayores comunes
    processed_text = re.sub(r'\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciséis|diecisiete|dieciocho|diecinueve|veinte|veintiuno|veintidós|veintitrés|veinticuatro|veinticinco|veintiséis|veintisiete|veintiocho|veintinueve|treinta)\.\s+', r'\1, ', processed_text)
    # También reemplazar números en dígitos seguidos de punto
    processed_text = re.sub(r'\b(\d{1,2})\.\s+', r'\1, ', processed_text)
    processed_text = re.sub(r'\s+', ' ', processed_text).strip() # Normalize whitespace
    return processed_text

def split_sentences(text):
    """
    Divide texto en oraciones de manera inteligente para síntesis de voz.
    
    Utiliza múltiples estrategias para evitar divisiones incorrectas que afectarían
    la naturalidad del audio generado:
    
    1. Detecta abreviaciones comunes (Sr., Dr., etc.)
    2. Preserva acrónimos y URLs (U.S.A., www.ejemplo.com)
    3. Maneja números decimales (3.14, $1.99)
    4. Reconoce diálogos y citas
    5. Procesa múltiples idiomas
    
    Args:
        text (str): Texto a dividir en oraciones
        
    Returns:
        list[str]: Lista de oraciones limpias y no vacías
        
    Examples:
        >>> split_sentences("Hola Sr. García. ¿Cómo está? ¡Muy bien!")
        ["Hola Sr. García.", "¿Cómo está?", "¡Muy bien!"]
        
        >>> split_sentences("Cuesta $19.99. Es barato.")
        ["Cuesta $19.99.", "Es barato."]
    """
    if not text or not text.strip():
        return []
    
    # Abreviaciones comunes en múltiples idiomas (expandida para evitar cortes)
    abbreviations = {
        'es': r'(?:Sr|Sra|Srta|Dr|Dra|Prof|Profa|Lic|Licda|Ing|Inga|Arq|Arqa|Mtro|Mtra|etc|vs|p\.ej|i\.e|cf|vol|cap|art|núm|pág|ed|op\.cit)',
        'en': r'(?:Mr|Mrs|Ms|Miss|Dr|Prof|Inc|Ltd|Corp|Co|vs|e\.g|i\.e|cf|vol|ch|art|no|pg|ed|op\.cit)',
        'fr': r'(?:M|Mme|Mlle|Dr|Prof|etc|vs|p\.ex|c\.à\.d|cf|vol|ch|art|n°|p|éd)',
        'de': r'(?:Hr|Fr|Frl|Dr|Prof|etc|vs|z\.B|d\.h|vgl|Bd|Kap|Art|Nr|S|Hrsg)',
        'it': r'(?:Sig|Sig\.ra|Sig\.na|Dr|Prof|ecc|vs|ad\.es|cioè|cfr|vol|cap|art|n|p|ed)',
        'pt': r'(?:Sr|Sra|Srta|Dr|Dra|Prof|Profa|etc|vs|p\.ex|ou\.seja|cf|vol|cap|art|n|p|ed)'
    }
    
    # Combinar todas las abreviaciones
    all_abbrevs = '|'.join(abbreviations.values())
    
    # Usar método simple más confiable para evitar errores de regex
    # Dividir por puntuación seguida de espacio y mayúscula, pero proteger abreviaciones
    sentences = []
    current_sentence = ""
    
    # Dividir por oraciones pero verificar abreviaciones
    parts = re.split(r'([.!?¡¿…]+\s*)', text)
    
    for i, part in enumerate(parts):
        current_sentence += part
        
        # Si termina en puntuación
        if re.match(r'[.!?¡¿…]+\s*$', part):
            # Verificar si la oración anterior termina en abreviación
            words = current_sentence.strip().split()
            if words:
                last_word = words[-1] if len(words) > 1 else ""
                # Si no termina en abreviación conocida, es fin de oración
                is_abbreviation = False
                for abbrev_pattern in abbreviations.values():
                    if re.match(rf'({abbrev_pattern})\.', last_word):
                        is_abbreviation = True
                        break
                
                if not is_abbreviation:
                    sentences.append(current_sentence.strip())
                    current_sentence = ""
    
    # Agregar cualquier texto restante
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    
    # Limpiar y filtrar oraciones
    cleaned_sentences = []
    for sentence in sentences:
        # Limpiar espacios y caracteres de control
        clean_sentence = re.sub(r'[\r\n\t]+', ' ', sentence).strip()
        
        # Filtrar oraciones muy cortas o que solo contienen puntuación
        if len(clean_sentence) > 2 and re.search(r'[a-zA-ZáéíóúñüÁÉÍÓÚÑÜ0-9]', clean_sentence):
            # Si la oración es muy larga (más de 500 caracteres), dividirla por comas
            if len(clean_sentence) > 500:
                # Dividir por comas y procesar cada parte
                comma_parts = [part.strip() for part in clean_sentence.split(',') if part.strip()]
                current_chunk = ""
                
                for part in comma_parts:
                    # Si agregar esta parte haría que el chunk sea muy largo, guardar el chunk actual
                    if len(current_chunk + ', ' + part) > 200 and current_chunk:
                        cleaned_sentences.append(current_chunk.strip())
                        current_chunk = part
                    else:
                        if current_chunk:
                            current_chunk += ', ' + part
                        else:
                            current_chunk = part
                
                # Agregar el último chunk si no está vacío
                if current_chunk.strip():
                    cleaned_sentences.append(current_chunk.strip())
            else:
                # Verificar que tenga al menos 3 palabras para dividir el texto (reducido de 7)
                word_count = len(re.findall(r'\b\w+\b', clean_sentence))
                if word_count >= 3:
                    cleaned_sentences.append(clean_sentence)
                else:
                    # Si tiene menos de 3 palabras, combinarla con la anterior si existe
                    if cleaned_sentences:
                        cleaned_sentences[-1] += ' ' + clean_sentence
                    else:
                        # Si es la primera, mantenerla si tiene contenido válido
                        if len(clean_sentence.strip()) > 2:
                            cleaned_sentences.append(clean_sentence)
        
    # Si no se pudo dividir correctamente, usar método de respaldo
    if not cleaned_sentences and text.strip():
        # Método de respaldo más simple
        fallback_sentences = re.split(r'[.!?]+\s+', text.strip())
        temp_sentences = [s.strip() for s in fallback_sentences if s.strip()]
        
        # Combinar oraciones muy cortas en el método de respaldo
        cleaned_sentences = []
        for sentence in temp_sentences:
            if len(sentence) > 2 and len(re.findall(r'\b\w+\b', sentence)) >= 3:
                cleaned_sentences.append(sentence)
            elif cleaned_sentences:
                # Combinar con la anterior si tiene menos de 3 palabras
                cleaned_sentences[-1] += ' ' + sentence
            else:
                # Si es la primera y es corta, mantenerla si tiene contenido válido
                if len(sentence.strip()) > 2:
                    cleaned_sentences.append(sentence)
    
    # Log the divided text with <> separators
    if cleaned_sentences:
        divided_text = ' <> '.join(cleaned_sentences)
        logging.info(f"[SPLIT] Text divided into {len(cleaned_sentences)} segments: {divided_text}")
    
    return cleaned_sentences

def filter_text_segment(text_segment, model_replacements):
    """
    Process text segment with comprehensive filtering and replacement logic.
    
    Args:
        text_segment (str): Raw text to process
        model_replacements (list): Model-specific replacements
    
    Returns:
        str: Processed text ready for TTS
    """
    logging.debug(f"[FILTER] Processing segment: '{text_segment[:100]}{'...' if len(text_segment) > 100 else ''}'")
    
    # Step 1: Remove code blocks
    text = filter_code_blocks(text_segment)
    logging.debug(f"[FILTER] After code block removal: '{text[:100]}{'...' if len(text) > 100 else ''}'")
    
    # Step 2: Process line breaks
    text = process_line_breaks(text)
    logging.debug(f"[FILTER] After line break processing: '{text[:100]}{'...' if len(text) > 100 else ''}'")
    
    # Step 3: Apply replacements - prioritize model-specific over global
    if model_replacements:
        logging.debug(f"[FILTER] Using {len(model_replacements)} model-specific replacements (ignoring global)")
        text = multiple_replace(text, model_replacements)
    elif global_replacements:
        logging.debug(f"[FILTER] No model replacements found, using {len(global_replacements)} global replacements")
        text = multiple_replace(text, global_replacements)
    else:
        logging.debug(f"[FILTER] No replacements available (no model or global replacements)")
    
    # Step 5: Final cleanup
    text = re.sub(r'\s+', ' ', text).strip() # Normalize whitespace
    
    # Removed the "punto y seguido" replacement that was causing issues
    # Keep periods as they are - only use commas for line breaks, not for existing periods
    
    # Removed character limit to allow full text processing
    
    logging.debug(f"[FILTER] Final processed text: '{text[:100]}{'...' if len(text) > 100 else ''}'")
    return text

def generate_silence(seconds, temp_dir):
    if seconds <= 0:
        return None
    output_file = os.path.join(temp_dir, f"silence_{random_string(4)}_{seconds}s.wav")
    try:
        subprocess.run(
            [
                ffmpeg_path, '-loglevel', 'error', '-f', 'lavfi',
                '-i', 'anullsrc=r=22050:cl=mono', '-t', str(seconds),
                '-ar', '22050', '-ac', '1', '-f', 'wav', '-y', output_file
            ],
            check=True, capture_output=True, text=True
        )
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            logging.debug(f"Generated silence file: {output_file}")
            return output_file
        else:
             logging.error(f"Silence file {output_file} was not created or is empty.")
             return None
    except Exception as e:
        logging.error(f"Error generating silence: {e}")
        return None

def generate_audio_for_sentence(text_part, model_path, settings, temp_dir, retry_attempts=3):
    if not text_part.strip():
        return None
    
    logging.debug(f"[PIPER] Generating audio for: '{text_part}'")
    logging.debug(f"[PIPER] Model: {os.path.basename(model_path)}")
    logging.debug(f"[PIPER] Settings: {settings}")
    
    # Piper expects specific sampling rate and channels (e.g., 22050 Hz, 1 channel mono)
    # Ensure the model file is accessible and valid
    if not os.path.exists(model_path):
        logging.error(f"[PIPER] Model path does not exist: {model_path}")
        return None

    base_output_name = f"audio_{random_string(8)}"
    output_file = os.path.join(temp_dir, f"{base_output_name}.wav")
    
    command = [
        piper_binary_path, '-m', model_path, '-f', output_file,
        '--speaker', str(settings.get('speaker', 0)),
        '--noise-scale', str(settings.get('noise_scale', 0.667)),
        '--length-scale', str(settings.get('length_scale', 1.0)),
        '--noise-w', str(settings.get('noise_w', 0.8)),
    ]
    
    # Add '--json-input' if your Piper version supports it for more robust input handling
    # command.append('--json-input') 
    
    for attempt in range(retry_attempts):
        try:
            logging.debug(f"[PIPER] Attempt {attempt+1}/{retry_attempts} to generate audio for: '{text_part[:50]}...'")
            logging.debug(f"[PIPER] Command: {' '.join(command)}")
            logging.debug(f"[PIPER] Input text (final): '{text_part}'")
            
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding='utf-8',
            )
            # Send text_part as stdin to piper
            stdout, stderr = process.communicate(input=text_part + '\n', timeout=60) # Reduced timeout for individual sentences
            
            if process.returncode == 0:
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    logging.debug(f"[PIPER] Successfully generated audio file: {output_file} ({os.path.getsize(output_file)} bytes)")
                    return output_file
                else:
                    logging.warning(f"[PIPER] Process succeeded but file is missing/empty: {output_file}. Stderr: {stderr}")
                    if os.path.exists(output_file): os.remove(output_file) # Clean up empty file
            else:
                logging.error(f"[PIPER] Process failed with return code {process.returncode} for text: '{text_part}'. Stderr: {stderr}")
                if os.path.exists(output_file): os.remove(output_file) # Clean up failed file
        except subprocess.TimeoutExpired:
            process.kill()
            logging.warning(f"Piper process timed out on attempt {attempt+1} for text: '{text_part[:50]}...'")
            if os.path.exists(output_file): os.remove(output_file) # Clean up timed out file
        except Exception as e:
            logging.error(f"Error during audio generation attempt {attempt+1} for text: '{text_part[:50]}...': {e}")
            if os.path.exists(output_file): os.remove(output_file) # Clean up on general error
        
        if attempt < retry_attempts - 1:
            time.sleep(0.5 * (attempt + 1)) # Exponential back-off for retries
            
    logging.error(f"Failed to generate audio for text after {retry_attempts} attempts: '{text_part[:50]}...'")
    return None

def concatenate_audio_files(audio_files, output_file, temp_dir):
    if not audio_files:
        logging.warning("No audio files provided for concatenation.")
        return None
    list_file = os.path.join(temp_dir, f'concat_list_{random_string(4)}.txt')
    try:
        with open(list_file, 'w', encoding='utf-8') as f:
            for file_item in audio_files:
                # Ensure path is correctly formatted for ffmpeg, especially on Windows
                abs_file_path = os.path.abspath(file_item).replace('\\', '/')
                f.write(f"file '{abs_file_path}'\n")
        
        logging.debug(f"Concatenating {len(audio_files)} files to {output_file}")
        subprocess.run(
            [
                ffmpeg_path, '-loglevel', 'error', '-f', 'concat',
                '-safe', '0', '-i', list_file, '-c', 'copy', '-y', output_file
            ],
            check=True, capture_output=True, text=True
        )
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            logging.debug(f"Concatenated audio files successfully to {output_file}")
            return True
        else:
            logging.error(f"Concatenated output file {output_file} is missing or empty after FFmpeg. Stderr: {subprocess.run([ffmpeg_path, '-f', 'concat', '-safe', '0', '-i', list_file, '-c', 'copy', '-y', output_file], capture_output=True, text=True).stderr}")
            return False
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg concatenation failed with error: {e.stderr}")
        return False
    except Exception as e:
        logging.error(f"Error concatenating audios: {e}")
        return False
    finally:
        if os.path.exists(list_file):
            try: os.remove(list_file)
            except Exception as e: logging.error(f"Error removing list file {list_file}: {e}")

def convert_text_to_speech_concurrent(text, default_model_name, settings):
    temp_dir = None
    all_temp_files = [] # Keep track of all generated temp files for cleanup
    final_output_mp3 = None
    error_message = None

    try:
        temp_dir = tempfile.mkdtemp(dir=temp_audio_folder)
        logging.info(f"Created temporary directory: {temp_dir}")
        audio_segments_to_concat = []

        # Resolve model name to actual key if needed
        resolved_model_name = model_id_to_filename_map.get(default_model_name, default_model_name)
        
        if resolved_model_name not in model_configs or not os.path.exists(model_configs[resolved_model_name]["model_path_onnx"]):
             error_message = f"Model '{default_model_name}' not found or its ONNX file is missing."
             logging.error(error_message)
             return None, error_message

        current_model_name = resolved_model_name
        current_model_config = model_configs[current_model_name]
        current_model_path = current_model_config["model_path_onnx"]
        current_replacements = current_model_config.get("replacements", [])
        
        # Split text by custom tags for model switching or silence
        segments = re.split(r'(<#.*?#>)', text)
        ordered_tasks = [] # Store futures and paths in order of processing

        for i, segment in enumerate(segments):
            if not segment.strip():
                continue
            
            processed_as_tag = False
            if segment.startswith('<#') and segment.endswith('#>'):
                silence_match = re.match(r'<#(\d+\.?\d*)#>', segment)
                if silence_match:
                    try:
                        seconds = float(silence_match.group(1))
                        # Generate silence directly, not via executor
                        silence_file = generate_silence(seconds, temp_dir)
                        if silence_file:
                            ordered_tasks.append({'type': 'silence', 'file': silence_file, 'duration': seconds})
                            all_temp_files.append(silence_file)
                        processed_as_tag = True
                    except ValueError:
                        logging.warning(f"Invalid silence duration in tag: {segment}. Ignoring tag.")
                        # Treat as regular text if tag is malformed
                else:
                    model_match = re.match(r'<#([\w-]+)#>', segment)
                    if model_match:
                        requested_model_key = model_match.group(1)
                        if requested_model_key == 'default':
                            current_model_name = default_model_name
                            current_model_config = model_configs[current_model_name]
                            current_model_path = current_model_config["model_path_onnx"]
                            current_replacements = current_model_config.get("replacements", [])
                            logging.debug(f"Switched to default model: {current_model_name}")
                            processed_as_tag = True
                        else:
                            # Try to resolve model key
                            resolved_requested_key = model_id_to_filename_map.get(requested_model_key, requested_model_key)
                            if resolved_requested_key in model_configs:
                                potential_model_config = model_configs[resolved_requested_key]
                                potential_model_path = potential_model_config.get("model_path_onnx")
                                if potential_model_path and os.path.exists(potential_model_path):
                                    current_model_name = resolved_requested_key
                                    current_model_config = potential_model_config
                                    current_model_path = potential_model_path
                                    current_replacements = current_model_config.get("replacements", [])
                                    logging.debug(f"Switched model to: {current_model_name} (requested: {requested_model_key})")
                                    processed_as_tag = True
                                else:
                                    logging.warning(f"Requested model '{requested_model_key}' not found or ONNX file missing. Continuing with current model.")
                            else:
                                logging.warning(f"Requested model '{requested_model_key}' not found. Continuing with current model.")
                    else:
                        logging.warning(f"Unrecognized custom tag: {segment}. Ignoring tag.")
            
            if processed_as_tag:
                continue

            # Process segment as regular text
            logging.debug(f"[TTS] Processing text segment with model '{current_model_name}': '{segment[:100]}{'...' if len(segment) > 100 else ''}'")
            
            filtered_segment = filter_text_segment(segment, current_replacements)
            if not filtered_segment.strip():
                logging.debug(f"[TTS] Segment became empty after filtering, skipping")
                continue
            
            logging.info(f"[TTS] Text ready for synthesis: '{filtered_segment}'")
            
            sentences = split_sentences(filtered_segment)
            logging.debug(f"[TTS] Split into {len(sentences)} sentences")
            
            for j, sentence in enumerate(sentences):
                 if sentence.strip():
                    logging.debug(f"[TTS] Sentence {j+1}/{len(sentences)}: '{sentence[:100]}{'...' if len(sentence) > 100 else ''}'")
                    future = executor.submit(generate_audio_for_sentence, sentence.strip(), current_model_path, settings, temp_dir)
                    ordered_tasks.append({'type': 'audio', 'future': future, 'sentence': sentence.strip()})

        # Collect results in order
        for task in ordered_tasks:
            if task['type'] == 'silence':
                if task['file'] and os.path.exists(task['file']) and os.path.getsize(task['file']) > 0:
                    audio_segments_to_concat.append(task['file'])
                else:
                    logging.warning(f"Skipping empty or missing silence file: {task['file']}")
            elif task['type'] == 'audio':
                try:
                    sentence_audio_file = task['future'].result()
                    if sentence_audio_file and os.path.exists(sentence_audio_file) and os.path.getsize(sentence_audio_file) > 0:
                        audio_segments_to_concat.append(sentence_audio_file)
                        all_temp_files.append(sentence_audio_file)
                    else:
                        logging.warning(f"Skipping empty or missing audio file for sentence: '{task['sentence'][:50]}...'")
                except Exception as exc:
                    logging.error(f"Exception retrieving audio generation result for sentence '{task['sentence'][:50]}...': {exc}")

        if not audio_segments_to_concat:
             error_message = "No audio segments were successfully generated or collected for concatenation."
             logging.warning(error_message)
             return None, error_message

        final_output_wav = os.path.join(temp_dir, f"final_output_{random_string(8)}.wav")
        if not concatenate_audio_files(audio_segments_to_concat, final_output_wav, temp_dir):
            error_message = "Failed to concatenate audio files into a final WAV."
            return None, error_message
        
        all_temp_files.append(final_output_wav) # Add the concatenated WAV for cleanup later

        compressed_output_mp3 = os.path.join(temp_audio_folder, f"converted_{random_string(8)}.mp3")
        try:
            # -qscale:a 2 is a good balance for MP3 quality
            subprocess.run(
                [
                    ffmpeg_path, '-loglevel', 'error', '-i', final_output_wav,
                    '-codec:a', 'libmp3lame', '-qscale:a', '2', '-y', compressed_output_mp3
                ],
                check=True, capture_output=True, text=True
            )
            if os.path.exists(compressed_output_mp3) and os.path.getsize(compressed_output_mp3) > 0:
                logging.info(f"Compressed final audio to: {compressed_output_mp3}")
                final_output_mp3 = compressed_output_mp3
            else:
                error_message = f"Final MP3 is missing or empty after compression. Stderr: {subprocess.run([ffmpeg_path, '-i', final_output_wav, '-codec:a', 'libmp3lame', '-qscale:a', '2', '-y', compressed_output_mp3], capture_output=True, text=True).stderr}"
                logging.error(error_message)
                final_output_mp3 = None
        except subprocess.CalledProcessError as e:
            error_message = f"FFmpeg compression failed with error: {e.stderr}"
            logging.error(error_message)
            final_output_mp3 = None
        except Exception as e:
            error_message = f"Error compressing audio: {e}"
            logging.error(error_message)
            final_output_mp3 = None

    except Exception as e:
        error_message = f"Unexpected error in conversion process: {e}"
        logging.error(error_message, exc_info=True)
        final_output_mp3 = None
    finally:
        # Clean up all temporary files generated during this conversion
        for file_path in all_temp_files:
            if os.path.exists(file_path):
                try: 
                    os.remove(file_path)
                    logging.debug(f"Cleaned up temporary file: {file_path}")
                except Exception as e: 
                    logging.error(f"Error cleaning temporary file {file_path}: {e}")
        # Clean up the temporary directory itself
        if temp_dir and os.path.exists(temp_dir):
            try: 
                shutil.rmtree(temp_dir)
                logging.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e: 
                logging.error(f"Error removing temporary directory {temp_dir}: {e}")

    return final_output_mp3, error_message

def get_client_ip():
    """Get the real client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr

def is_private_ip(ip):
    """Check if IP is from private network"""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback
    except:
        return False

def check_rate_limit(client_ip):
    """Check if client has exceeded rate limits"""
    now = datetime.now()
    
    # Clean old entries
    for ip in list(request_counts.keys()):
        request_counts[ip] = [(timestamp, count) for timestamp, count in request_counts[ip] 
                             if now - timestamp < timedelta(hours=1)]
        if not request_counts[ip]:
            del request_counts[ip]
    
    # Check if IP is blocked
    if client_ip in blocked_ips:
        if now - blocked_ips[client_ip] < timedelta(minutes=BLOCK_DURATION_MINUTES):
            return False, "IP temporarily blocked due to suspicious activity"
        else:
            del blocked_ips[client_ip]
    
    # Initialize or get current counts
    if client_ip not in request_counts:
        request_counts[client_ip] = []
    
    # Count requests in last minute and hour
    minute_ago = now - timedelta(minutes=1)
    hour_ago = now - timedelta(hours=1)
    
    requests_last_minute = sum(1 for timestamp, _ in request_counts[client_ip] if timestamp > minute_ago)
    requests_last_hour = sum(1 for timestamp, _ in request_counts[client_ip] if timestamp > hour_ago)
    
    # Check limits
    if requests_last_minute >= MAX_REQUESTS_PER_MINUTE:
        blocked_ips[client_ip] = now
        return False, "Rate limit exceeded: too many requests per minute"
    
    if requests_last_hour >= MAX_REQUESTS_PER_HOUR:
        blocked_ips[client_ip] = now
        return False, "Rate limit exceeded: too many requests per hour"
    
    # Add current request
    request_counts[client_ip].append((now, 1))
    return True, None

def validate_user_agent(user_agent):
    """Validate user agent to prevent automated requests"""
    if not user_agent:
        return False, "Missing User-Agent header"
    
    user_agent_lower = user_agent.lower()
    
    # Check if user agent is in blocked list
    if user_agent_lower in blocked_user_agents:
        return False, "Blocked User-Agent"
    
    # Check for suspicious patterns
    for suspicious in SUSPICIOUS_USER_AGENTS:
        if suspicious in user_agent_lower:
            blocked_user_agents.add(user_agent_lower)
            return False, f"Suspicious User-Agent detected: {suspicious}"
    
    # Check if user agent matches valid patterns (more lenient for legitimate browsers)
    is_valid = any(valid in user_agent_lower for valid in VALID_USER_AGENTS)
    if not is_valid:
        # Log suspicious user agent but don't block immediately
        logging.warning(f"Potentially suspicious User-Agent: {user_agent}")
        # For now, allow it but monitor
    
    return True, None

def validate_request_headers():
    """Validate request headers for security"""
    # Check for common automation headers
    suspicious_headers = [
        'x-requested-with', 'x-automation', 'x-test', 'x-bot',
        'selenium-remote-control', 'webdriver'
    ]
    
    for header in suspicious_headers:
        if request.headers.get(header):
            return False, f"Suspicious header detected: {header}"
    
    # Check Content-Type for POST requests
    if request.method == 'POST':
        content_type = request.headers.get('Content-Type', '')
        if not content_type:
            return False, "Missing Content-Type header"
        
        # For /convert endpoint, expect form data
        if request.endpoint == 'convert' and 'application/x-www-form-urlencoded' not in content_type:
            return False, "Invalid Content-Type for web interface"
    
    return True, None

def security_check(f):
    """Comprehensive security decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        
        # Skip security checks for local/private IPs in development
        if not is_private_ip(client_ip):
            # Rate limiting
            rate_ok, rate_msg = check_rate_limit(client_ip)
            if not rate_ok:
                logging.warning(f"Rate limit exceeded for IP {client_ip}: {rate_msg}")
                return jsonify({'error': 'Too many requests. Please try again later.'}), 429
            
            # User agent validation
            ua_ok, ua_msg = validate_user_agent(user_agent)
            if not ua_ok:
                logging.warning(f"Invalid User-Agent from IP {client_ip}: {ua_msg}")
                return jsonify({'error': 'Invalid request. Please use a standard web browser.'}), 403
            
            # Header validation
            header_ok, header_msg = validate_request_headers()
            if not header_ok:
                logging.warning(f"Invalid headers from IP {client_ip}: {header_msg}")
                return jsonify({'error': 'Invalid request headers.'}), 403
        
        # Additional validation for text input
        if request.method == 'POST':
            if request.is_json:
                data = request.get_json()
                text = data.get('text', '') if data else ''
            else:
                text = request.form.get('text', '')
            
            if text and len(text) > MAX_TEXT_LENGTH:
                logging.warning(f"Text too long from IP {client_ip}: {len(text)} characters")
                return jsonify({'error': f'Text too long. Maximum {MAX_TEXT_LENGTH} characters allowed.'}), 400
            
            # Check for potential injection attempts
            suspicious_patterns = ['<script', '<?php', '<%', 'javascript:', 'data:', 'vbscript:']
            text_lower = text.lower() if text else ''
            for pattern in suspicious_patterns:
                if pattern in text_lower:
                    logging.warning(f"Suspicious content detected from IP {client_ip}: {pattern}")
                    return jsonify({'error': 'Invalid content detected.'}), 400
        
        return f(*args, **kwargs)
    return decorated_function

# Función para convertir imagen a base64
def image_to_base64(image_path):
    try:
        if os.path.exists(image_path):
            with open(image_path, 'rb') as img_file:
                return base64.b64encode(img_file.read()).decode('utf-8')
        return None
    except Exception as e:
        logging.error(f"Error converting image to base64: {e}")
        return None

# Función para eliminar archivos de forma diferida
def cleanup_file_delayed(filepath, delay=1.0):
    """Elimina un archivo después de un pequeño delay para asegurar que Flask termine de usarlo"""
    def delayed_cleanup():
        time.sleep(delay)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logging.debug(f"Final MP3 deleted after delay: {filepath}")
        except Exception as e:
            logging.error(f"Error deleting final MP3 after delay {filepath}: {e}")
    
    # Ejecutar la limpieza en un hilo separado
    import threading
    cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
    cleanup_thread.start()

# Función para enviar archivo como stream en memoria
def send_file_as_stream(filepath, mimetype='audio/mpeg', filename=None):
    """Envía un archivo como stream y permite eliminarlo inmediatamente"""
    try:
        with open(filepath, 'rb') as f:
            file_data = f.read()
        
        # Eliminar el archivo inmediatamente después de leerlo
        try:
            os.remove(filepath)
            logging.debug(f"Final MP3 deleted immediately after reading: {filepath}")
        except Exception as e:
            logging.error(f"Error deleting final MP3 immediately {filepath}: {e}")
        
        # Crear un stream en memoria
        file_stream = io.BytesIO(file_data)
        
        # Usar Response para enviar el stream
        if not filename:
            filename = f'audio_{random_string(8)}.mp3'
            
        return Response(
            file_stream.getvalue(),
            mimetype=mimetype,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(file_data))
            }
        )
    except Exception as e:
        logging.error(f"Error sending file as stream: {e}")
        return None

@app.route('/convert', methods=['POST'])
@security_check
def convert():
    """Convertir texto a voz para la interfaz web del playground"""
    data = request.form
    text = data.get('text')
    model_name = data.get('model')
    
    if not text or not text.strip():
        return jsonify({'error': 'El texto no puede estar vacío'}), 400
    if not model_name:
        return jsonify({'error': 'Se requiere un nombre de modelo'}), 400
    
    # Resolve model name to actual key if needed
    resolved_model_name = model_id_to_filename_map.get(model_name, model_name)
    if resolved_model_name not in existing_models:
        return jsonify({'error': f'Modelo "{model_name}" no encontrado'}), 404
    
    settings = {
        'speaker': int(data.get('speaker', 0)),
        'noise_scale': float(data.get('noise_scale', 0.667)),
        'length_scale': float(data.get('length_scale', 1.0)),
        'noise_w': float(data.get('noise_w', 0.8)),
    }
    
    output_file_mp3, error_message = convert_text_to_speech_concurrent(text, model_name, settings)
    
    if output_file_mp3:
        # Read the MP3 file and encode it as base64 for direct embedding in HTML
        try:
            with open(output_file_mp3, "rb") as audio_file:
                audio_base64 = base64.b64encode(audio_file.read()).decode('utf-8')
            
            # Clean up the temporary file
            os.remove(output_file_mp3)
            
            # Return the base64 encoded audio data
            return jsonify({'audio_base64': audio_base64})
        except Exception as e:
            logging.error(f"Error encoding audio file: {e}")
            return jsonify({'error': 'Error procesando archivo de audio'}), 500
    else:
        logging.error(f"Audio conversion failed. Error: {error_message}")
        return jsonify({'error': error_message or 'Error al convertir texto a voz'}), 500

if __name__ == '__main__':
    logging.info("Iniciando la API de texto a voz...")
    
    # Verificaciones de dependencias
    if not os.path.exists(piper_binary_path): 
        logging.error(f"ERROR: Piper binary no encontrado en {piper_binary_path}. Asegúrate de que el ejecutable esté en la carpeta 'piper'.")
    else:
        logging.info(f"Piper binary encontrado en {piper_binary_path}")

    # Verifica si ffmpeg_path es una ruta válida y el ejecutable existe
    ffmpeg_ok = False
    if os.path.exists(ffmpeg_path):
        try:
            # Intenta ejecutar ffmpeg para verificar que es funcional
            subprocess.run([ffmpeg_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logging.info(f"FFmpeg binary encontrado y funcional en {ffmpeg_path}")
            ffmpeg_ok = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            logging.error(f"ERROR: FFmpeg binary encontrado en {ffmpeg_path} pero no es ejecutable o no funciona correctamente. Verifica tu instalación de FFmpeg.")
    else:
        logging.error(f"ERROR: FFmpeg binary no encontrado en {ffmpeg_path}. Por favor, instale FFmpeg o asegúrese de que esté en el PATH o en la carpeta 'ffmpeg'.")

    if not existing_models: 
        logging.warning("ADVERTENCIA: No se encontraron modelos .onnx válidos en la carpeta 'models'.")
    
    logging.info(f"Token de API interno configurado. Modelos disponibles: {existing_models}")
    
    app.run(host='0.0.0.0', port=7860, debug=False)