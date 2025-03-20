import os
import sys
import requests
import json
import time
import wave
import numpy as np
import sounddevice as sd
import argparse
import threading
import queue
import asyncio
import re
from pydub import AudioSegment
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import html2text
import subprocess
from datetime import timedelta

# LM Studio API settings
API_URL = "http://127.0.0.1:1234/v1/completions"
HEADERS = {
    "Content-Type": "application/json"
}

# Model parameters
MAX_TOKENS = 1200
TEMPERATURE = 0.6
TOP_P = 0.9
REPETITION_PENALTY = 1.3
SAMPLE_RATE = 24000  # SNAC model uses 24kHz
MAX_CHUNK_LENGTH = 150  # Maximum number of characters per chunk
TEMP_DIR = "temp_chunks"  # Directory for temporary chunk WAV files

# Available voices based on the Orpheus-TTS repository
AVAILABLE_VOICES = ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]
DEFAULT_VOICE = "tara"  # Best voice according to documentation

# Special token IDs for Orpheus model
START_TOKEN_ID = 128259
END_TOKEN_IDS = [128009, 128260, 128261, 128257]
CUSTOM_TOKEN_PREFIX = "<custom_token_"

def format_prompt(prompt, voice=DEFAULT_VOICE):
    """Format prompt for Orpheus model with voice prefix and special tokens."""
    if voice not in AVAILABLE_VOICES:
        print(f"Warning: Voice '{voice}' not recognized. Using '{DEFAULT_VOICE}' instead.")
        voice = DEFAULT_VOICE
        
    # Format similar to how engine_class.py does it with special tokens
    formatted_prompt = f"{voice}: {prompt}"
    
    # Add special token markers for the LM Studio API
    special_start = "<|audio|>"  # Using the additional_special_token from config
    special_end = "<|eot_id|>"   # Using the eos_token from config
    
    return f"{special_start}{formatted_prompt}{special_end}"

def generate_tokens_from_api(prompt, voice=DEFAULT_VOICE, temperature=TEMPERATURE, 
                            top_p=TOP_P, max_tokens=MAX_TOKENS, repetition_penalty=REPETITION_PENALTY):
    """Generate tokens from text using LM Studio API."""
    formatted_prompt = format_prompt(prompt, voice)
    print(f"Generating speech for: {formatted_prompt}")
    
    # Create the request payload for the LM Studio API
    payload = {
        "model": "orpheus-3b-0.1-ft-q4_k_m",  # Model name can be anything, LM Studio ignores it
        "prompt": formatted_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "repeat_penalty": repetition_penalty,
        "stream": True
    }
    
    # Make the API request with streaming
    response = requests.post(API_URL, headers=HEADERS, json=payload, stream=True)
    
    if response.status_code != 200:
        print(f"Error: API request failed with status code {response.status_code}")
        print(f"Error details: {response.text}")
        return
    
    # Process the streamed response
    token_counter = 0
    for line in response.iter_lines():
        if line:
            line = line.decode('utf-8')
            if line.startswith('data: '):
                data_str = line[6:]  # Remove the 'data: ' prefix
                if data_str.strip() == '[DONE]':
                    break
                    
                try:
                    data = json.loads(data_str)
                    if 'choices' in data and len(data['choices']) > 0:
                        token_text = data['choices'][0].get('text', '')
                        token_counter += 1
                        if token_text:
                            yield token_text
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON: {e}")
                    continue
    
    print("Token generation complete")

def turn_token_into_id(token_string, index):
    """Convert token string to numeric ID for audio processing."""
    # Strip whitespace
    token_string = token_string.strip()
    
    # Find the last token in the string
    last_token_start = token_string.rfind(CUSTOM_TOKEN_PREFIX)
    
    if last_token_start == -1:
        return None
    
    # Extract the last token
    last_token = token_string[last_token_start:]
    
    # Process the last token
    if last_token.startswith(CUSTOM_TOKEN_PREFIX) and last_token.endswith(">"):
        try:
            number_str = last_token[14:-1]
            token_id = int(number_str) - 10 - ((index % 7) * 4096)
            return token_id
        except ValueError:
            return None
    else:
        return None

def convert_to_audio(multiframe, count):
    """Convert token frames to audio."""
    # Import here to avoid circular imports
    from decoder import convert_to_audio as orpheus_convert_to_audio
    return orpheus_convert_to_audio(multiframe, count)

async def tokens_decoder(token_gen):
    """Asynchronous token decoder that converts token stream to audio stream."""
    buffer = []
    count = 0
    async for token_text in token_gen:
        token = turn_token_into_id(token_text, count)
        if token is not None and token > 0:
            buffer.append(token)
            count += 1
            
            # Convert to audio when we have enough tokens
            if count % 7 == 0 and count > 27:
                buffer_to_proc = buffer[-28:]
                audio_samples = convert_to_audio(buffer_to_proc, count)
                if audio_samples is not None:
                    yield audio_samples

def tokens_decoder_sync(syn_token_gen, output_file=None):
    """Synchronous wrapper for the asynchronous token decoder."""
    audio_queue = queue.Queue()
    audio_segments = []
    
    # If output_file is provided, prepare WAV file
    wav_file = None
    if output_file:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        wav_file = wave.open(output_file, "wb")
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
    
    # Convert the synchronous token generator into an async generator
    async def async_token_gen():
        for token in syn_token_gen:
            yield token

    async def async_producer():
        async for audio_chunk in tokens_decoder(async_token_gen()):
            audio_queue.put(audio_chunk)
        audio_queue.put(None)  # Sentinel to indicate completion

    def run_async():
        asyncio.run(async_producer())

    # Start the async producer in a separate thread
    thread = threading.Thread(target=run_async)
    thread.start()

    # Process audio as it becomes available
    while True:
        audio = audio_queue.get()
        if audio is None:
            break
        
        audio_segments.append(audio)
        
        # Write to WAV file if provided
        if wav_file:
            wav_file.writeframes(audio)
    
    # Close WAV file if opened
    if wav_file:
        wav_file.close()
    
    thread.join()
    
    # Calculate and print duration
    duration = sum([len(segment) // (2 * 1) for segment in audio_segments]) / SAMPLE_RATE
    print(f"Generated {len(audio_segments)} audio segments")
    print(f"Generated {duration:.2f} seconds of audio")
    
    return audio_segments

def stream_audio(audio_buffer):
    """Stream audio buffer to output device."""
    if audio_buffer is None or len(audio_buffer) == 0:
        return
    
    # Convert bytes to NumPy array (16-bit PCM)
    audio_data = np.frombuffer(audio_buffer, dtype=np.int16)
    
    # Normalize to float in range [-1, 1] for playback
    audio_float = audio_data.astype(np.float32) / 32767.0
    
    # Play the audio
    sd.play(audio_float, SAMPLE_RATE)
    sd.wait()

def generate_speech_from_api(prompt, voice=DEFAULT_VOICE, output_file=None, temperature=TEMPERATURE, 
                     top_p=TOP_P, max_tokens=MAX_TOKENS, repetition_penalty=REPETITION_PENALTY):
    """Generate speech from text using Orpheus model via LM Studio API."""
    return tokens_decoder_sync(
        generate_tokens_from_api(
            prompt=prompt, 
            voice=voice,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            repetition_penalty=repetition_penalty
        ),
        output_file=output_file
    )

def list_available_voices():
    """List all available voices with the recommended one marked."""
    print("Available voices (in order of conversational realism):")
    for i, voice in enumerate(AVAILABLE_VOICES):
        marker = "★" if voice == DEFAULT_VOICE else " "
        print(f"{marker} {voice}")
    print(f"\nDefault voice: {DEFAULT_VOICE}")
    
    print("\nAvailable emotion tags:")
    print("<laugh>, <chuckle>, <sigh>, <cough>, <sniffle>, <groan>, <yawn>, <gasp>")

def read_text_from_file(file_path):
    """Read text from a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            text = file.read().strip()
            if not text:
                print(f"Warning: File '{file_path}' is empty.")
                return None
            return text
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return None
    except Exception as e:
        print(f"Error reading file '{file_path}': {e}")
        return None

def chunk_text(text, max_length=MAX_CHUNK_LENGTH):
    """Split text into smaller chunks at sentence boundaries."""
    # Ensure we don't have chunks that are too small
    min_length = max(50, max_length // 10)
    
    # First split by paragraph boundaries
    paragraphs = text.split('\n')
    paragraphs = [p for p in paragraphs if p.strip()]
    
    chunks = []
    current_chunk = ""
    
    for paragraph in paragraphs:
        # If paragraph is already longer than max_length, split it
        if len(paragraph) > max_length:
            # Split by sentence
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            for sentence in sentences:
                if len(sentence) > max_length:
                    # Very long sentence, split by commas or other punctuation
                    subparts = re.split(r'(?<=[,;:])\s+', sentence)
                    for part in subparts:
                        if len(current_chunk) + len(part) <= max_length:
                            current_chunk += part + " "
                        else:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = part + " "
                elif len(current_chunk) + len(sentence) <= max_length:
                    current_chunk += sentence + " "
                else:
                    chunks.append(current_chunk.strip())
                    current_chunk = sentence + " "
        elif len(current_chunk) + len(paragraph) + 1 <= max_length:
            current_chunk += paragraph + "\n"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = paragraph + "\n"
    
    # Add the last chunk if it has content
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Ensure we don't have chunks that are too small (merge with next chunk)
    i = 0
    while i < len(chunks) - 1:
        if len(chunks[i]) < min_length:
            if len(chunks[i]) + len(chunks[i+1]) <= max_length:
                chunks[i] = chunks[i] + " " + chunks[i+1]
                chunks.pop(i+1)
            else:
                i += 1
        else:
            i += 1
            
    return chunks

def ensure_directory_exists(directory):
    """Ensure that a directory exists, create it if it doesn't."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def merge_wav_files(wav_files, output_file):
    """Merge multiple WAV files into a single WAV file."""
    combined = AudioSegment.empty()
    
    for wav_file in wav_files:
        audio = AudioSegment.from_wav(wav_file)
        combined += audio
    
    combined.export(output_file, format="wav")
    print(f"All chunks merged into: {output_file}")
    print(f"Total duration: {len(combined) / 1000:.2f} seconds")
    
    return output_file

def get_audio_duration(file_path):
    """Get duration of an audio file in seconds."""
    audio = AudioSegment.from_file(file_path)
    return len(audio) / 1000.0  # Duration in seconds

def process_text_in_chunks(text, voice=DEFAULT_VOICE, output_file=None, temperature=TEMPERATURE,
                          top_p=TOP_P, repetition_penalty=REPETITION_PENALTY, max_tokens=MAX_TOKENS,
                          chapter_info=None):
    """Process text in chunks and merge into a single output file."""
    chunks = chunk_text(text)
    
    # Enhanced logging
    if chapter_info:
        print(f"\n{'='*80}")
        print(f"CHAPTER: {chapter_info['title']} - {chapter_info['index']+1}/{chapter_info['total']}")
        print(f"{'='*80}")
    
    print(f"Text split into {len(chunks)} chunks")
    
    # Create temp directory for chunk outputs
    ensure_directory_exists(TEMP_DIR)
    
    chunk_files = []
    total_start_time = time.time()
    
    for i, chunk in enumerate(chunks):
        # Enhanced logging with chapter info
        if chapter_info:
            print(f"\nProcessing chunk {i+1}/{len(chunks)} of CHAPTER {chapter_info['index']+1}: '{chapter_info['title']}'")
        else:
            print(f"\nProcessing chunk {i+1}/{len(chunks)}")
            
        print(f"Chunk size: {len(chunk)} characters")
        
        # Generate a temporary file name for this chunk
        temp_output_file = os.path.join(TEMP_DIR, f"chunk_{i+1:03d}.wav")
        chunk_files.append(temp_output_file)
        
        # Generate speech for this chunk
        chunk_start_time = time.time()
        generate_speech_from_api(
            prompt=chunk,
            voice=voice,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            max_tokens=max_tokens,
            output_file=temp_output_file
        )
        chunk_end_time = time.time()
        
        # Enhanced logging with chapter info
        if chapter_info:
            print(f"Chunk {i+1}/{len(chunks)} of CHAPTER {chapter_info['index']+1} completed in {chunk_end_time - chunk_start_time:.2f} seconds")
        else:
            print(f"Chunk {i+1} completed in {chunk_end_time - chunk_start_time:.2f} seconds")
    
    # Merge all chunks into the final output file
    if not output_file:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = f"outputs/{voice}_{timestamp}_combined.wav"
        
    # Create the outputs directory if it doesn't exist
    ensure_directory_exists(os.path.dirname(output_file))
    
    # Merge the chunks
    merge_wav_files(chunk_files, output_file)
    
    total_end_time = time.time()
    print(f"Total processing time: {total_end_time - total_start_time:.2f} seconds")
    
    # Cleanup temp files if needed (commented out for now)
    # for file in chunk_files:
    #     os.remove(file)
    # os.rmdir(TEMP_DIR)
    
    return output_file

# === New functions for EPUB handling ===

def html_to_text(html_content):
    """Convert HTML content to plain text."""
    # Parse HTML content
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.extract()
    
    # Convert remaining HTML to text
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_tables = False
    h.ignore_emphasis = False
    h.body_width = 0  # Don't wrap text
    
    text = h.handle(str(soup))
    
    # Clean up the text
    text = re.sub(r'\n{3,}', '\n\n', text)  # Replace multiple newlines with just two
    text = re.sub(r'\[.*?\]', '', text)     # Remove any remaining [image] tags
    
    return text.strip()

def extract_chapters_from_epub(epub_path):
    """Extract chapters from an EPUB file."""
    try:
        # Load the EPUB file
        book = epub.read_epub(epub_path)
        
        # Get the title of the book
        book_title = book.get_metadata('DC', 'title')
        if book_title:
            book_title = book_title[0][0]
        else:
            book_title = os.path.basename(epub_path).replace('.epub', '')
        
        print(f"Processing book: {book_title}")
        
        # Extract chapters
        chapters = []
        chapter_counter = 0
        
        # Loop through all the items in the EPUB
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                # Extract content
                content = item.get_content().decode('utf-8')
                
                # Skip if this is likely not a chapter (too short)
                if len(content) < 500:  # Arbitrary threshold
                    continue
                
                # Get title from the content if possible
                soup = BeautifulSoup(content, 'html.parser')
                title_tag = soup.find(['h1', 'h2', 'h3'])
                if title_tag:
                    chapter_title = title_tag.get_text().strip()
                else:
                    chapter_counter += 1
                    chapter_title = f"Chapter {chapter_counter}"
                
                # Convert HTML to text
                text = html_to_text(content)
                
                # Skip if the chapter is too short after conversion
                if len(text) < 200:  # Arbitrary threshold
                    continue
                
                # Add chapter to the list
                chapters.append({
                    'title': chapter_title,
                    'content': text
                })
                
                print(f"  Found chapter: {chapter_title} ({len(text)} characters)")
        
        return book_title, chapters
    
    except Exception as e:
        print(f"Error processing EPUB file: {e}")
        return None, []


def convert_wav_to_m4b(wav_file, output_file, chapter_info_list=None):
    """Convert WAV file to M4B with chapter information."""
    try:
        # Check if ffmpeg is installed
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            print("ERROR: ffmpeg is not installed or not in PATH. Please install ffmpeg to use this feature.")
            return None

        # Create metadata file for chapters if chapter_info_list is provided
        chapters_file = None
        if chapter_info_list and len(chapter_info_list) > 0:
            chapters_file = os.path.splitext(output_file)[0] + "_chapters.txt"
            with open(chapters_file, 'w', encoding='utf-8') as f:
                # Write the global metadata header
                f.write(";FFMETADATA1\n")

                # Add chapter markers
                for chapter in chapter_info_list:
                    f.write("[CHAPTER]\n")
                    f.write(f"TIMEBASE=1/1000\n")
                    f.write(f"START={int(chapter['start_time'] * 1000)}\n")
                    f.write(f"END={int(chapter['end_time'] * 1000)}\n")
                    f.write(f"title={chapter['title']}\n\n")

            print(f"Created chapter metadata file: {chapters_file}")

        # Build the ffmpeg command correctly
        cmd = ["ffmpeg"]

        # First add all input files with their options
        cmd.extend(["-i", wav_file])

        if chapters_file:
            cmd.extend(["-i", chapters_file])

        # Then add all output options
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        if chapters_file:
            cmd.extend(["-map_metadata", "1"])

        # Finally add the output file
        cmd.append(output_file)

        print(f"Converting to M4B with ffmpeg command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

        print(f"Successfully converted to M4B: {output_file}")
        return output_file

    except Exception as e:
        print(f"Error converting to M4B: {e}")
        return None

def merge_chapter_wav_files(chapter_files, output_wav, create_m4b=True):
    """Merge multiple chapter WAV files into a single WAV and optionally M4B file with chapter markers."""
    print(f"\n{'='*80}")
    print("MERGING ALL CHAPTERS INTO SINGLE AUDIOBOOK")
    print(f"{'='*80}")
    
    # Get chapter information
    chapter_info_list = []
    current_position = 0.0  # Start time in seconds
    
    # Create a list of chapter info with start and end times
    for i, chapter_file in enumerate(chapter_files):
        chapter_title = os.path.basename(chapter_file).split('_', 1)[1].rsplit('.', 1)[0]
        duration = get_audio_duration(chapter_file)
        
        chapter_info = {
            'index': i,
            'title': chapter_title,
            'file': chapter_file,
            'start_time': current_position,
            'end_time': current_position + duration,
            'duration': duration
        }
        
        chapter_info_list.append(chapter_info)
        current_position += duration
        
        print(f"Chapter {i+1}: '{chapter_title}' - Duration: {timedelta(seconds=duration)}")
    
    # Merge WAV files
    combined = AudioSegment.empty()
    
    for chapter_info in chapter_info_list:
        audio = AudioSegment.from_wav(chapter_info['file'])
        combined += audio
    
    # Export to WAV
    combined.export(output_wav, format="wav")
    print(f"\nAll chapters merged into WAV file: {output_wav}")
    print(f"Total duration: {timedelta(seconds=len(combined)/1000)}")
    
    # Create M4B version if requested
    if create_m4b:
        output_m4b = os.path.splitext(output_wav)[0] + ".m4b"
        convert_wav_to_m4b(output_wav, output_m4b, chapter_info_list)
        
    return output_wav

def process_epub_to_speech(epub_path, voice=DEFAULT_VOICE, output_dir=None, temperature=TEMPERATURE,
                          top_p=TOP_P, repetition_penalty=REPETITION_PENALTY, max_tokens=MAX_TOKENS):
    """Process an EPUB file and generate speech for each chapter."""
    # Extract chapters from the EPUB
    book_title, chapters = extract_chapters_from_epub(epub_path)
    
    if not chapters:
        print("No chapters found or error processing EPUB.")
        return
    
    # Create output directory
    if not output_dir:
        book_name = book_title.replace(' ', '_').replace(':', '').replace('/', '_')
        output_dir = f"outputs/epub_{book_name}"
    
    ensure_directory_exists(output_dir)
    
    # List chapters and ask for confirmation
    print("\nFound the following chapters:")
    for i, chapter in enumerate(chapters):
        print(f"{i+1}. {chapter['title']} ({len(chapter['content'])} characters)")
    
    # Ask for confirmation before proceeding
    proceed = input("\nDo you want to proceed with generating speech for these chapters? (y/n): ")
    if proceed.lower() not in ['y', 'yes']:
        print("Operation cancelled.")
        return
    
    # Process each chapter
    chapter_files = []
    for i, chapter in enumerate(chapters):
        print(f"\n{'='*80}")
        print(f"PROCESSING CHAPTER {i+1}/{len(chapters)}")
        print(f"TITLE: {chapter['title']}")
        print(f"{'='*80}")
        
        # Create a safe filename from the chapter title
        safe_title = re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
        output_file = os.path.join(output_dir, f"{i+1:03d}_{safe_title}.wav")
        
        # Process the chapter text with chapter info
        chapter_info = {
            'index': i,
            'title': chapter['title'],
            'total': len(chapters)
        }
        
        process_text_in_chunks(
            text=chapter['content'],
            voice=voice,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            max_tokens=max_tokens,
            output_file=output_file,
            chapter_info=chapter_info
        )
        
        chapter_files.append(output_file)
        print(f"Chapter {i+1}/{len(chapters)} completed and saved to {output_file}")
    
    # Merge all chapter files into a single WAV file
    if chapter_files:
        output_wav = os.path.join(output_dir, f"{book_name}_complete.wav")
        merge_chapter_wav_files(chapter_files, output_wav, create_m4b=True)
    
    print(f"\nAll chapters processed. Audio files saved to {output_dir}")
    print(f"Individual chapter WAV files are preserved in the same directory.")
    return output_dir

def main():
    global MAX_CHUNK_LENGTH
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Orpheus Text-to-Speech using LM Studio API")
    parser.add_argument("--text", type=str, help="Text to convert to speech")
    parser.add_argument("--file", type=str, help="Path to text file to read input from")
    parser.add_argument("--epub", type=str, help="Path to EPUB file to process")
    parser.add_argument("--voice", type=str, default=DEFAULT_VOICE, help=f"Voice to use (default: {DEFAULT_VOICE})")
    parser.add_argument("--output", type=str, help="Output WAV file path or directory")
    parser.add_argument("--list-voices", action="store_true", help="List available voices")
    parser.add_argument("--temperature", type=float, default=TEMPERATURE, help="Temperature for generation")
    parser.add_argument("--top_p", type=float, default=TOP_P, help="Top-p sampling parameter")
    parser.add_argument("--repetition_penalty", type=float, default=REPETITION_PENALTY, 
                       help="Repetition penalty (>=1.1 required for stable generation)")
    parser.add_argument("--chunk", action="store_true", help="Process text in smaller chunks")
    parser.add_argument("--chunk-size", type=int, default=MAX_CHUNK_LENGTH, 
                       help=f"Maximum characters per chunk (default: {MAX_CHUNK_LENGTH})")
    parser.add_argument("--no-m4b", action="store_true", help="Don't create M4B file (WAV only)")
    
    args = parser.parse_args()
    
    if args.list_voices:
        list_available_voices()
        return
    
    # Update chunk size if specified
    if args.chunk_size:
        MAX_CHUNK_LENGTH = args.chunk_size
    
    # Process EPUB if provided
    if args.epub:
        if not os.path.exists(args.epub):
            print(f"Error: EPUB file '{args.epub}' not found.")
            return
        
        process_epub_to_speech(
            epub_path=args.epub,
            voice=args.voice,
            output_dir=args.output,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
            max_tokens=MAX_TOKENS
        )
        return
    
    # Check for input text in this priority: command line text, file input, positional args, default
    prompt = None
    
    # 1. Check for --text argument
    if args.text:
        prompt = args.text
    
    # 2. Check for --file argument
    elif args.file:
        file_text = read_text_from_file(args.file)
        if file_text:
            prompt = file_text
        else:
            print("Error reading from file. Please provide valid text input.")
            return
    
    # 3. Check for positional arguments
    elif len(sys.argv) > 1 and sys.argv[1] not in ("--voice", "--output", "--temperature", "--top_p", "--repetition_penalty", "--file", "--chunk", "--chunk-size", "--epub", "--no-m4b"):
        prompt = " ".join([arg for arg in sys.argv[1:] if not arg.startswith("--")])
    
    # 4. If no input is provided, prompt the user
    if not prompt:
        prompt = input("Enter text to synthesize: ")
        if not prompt:
            prompt = "Hello, I am Orpheus, an AI assistant with emotional speech capabilities."

    # Default output file if none provided
    output_file = args.output
    if not output_file:
        # Create outputs directory if it doesn't exist
        os.makedirs("outputs", exist_ok=True)
        # Generate a filename based on the voice and a timestamp
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = f"outputs/{args.voice}_{timestamp}.wav"
        print(f"No output file specified. Saving to {output_file}")

    # Generate speech
    start_time = time.time()

    # Use chunking method if requested or text is long
    if args.chunk or len(prompt) > MAX_CHUNK_LENGTH:
        if not args.chunk and len(prompt) > MAX_CHUNK_LENGTH:
            print(f"Text is longer than {MAX_CHUNK_LENGTH} characters, automatically using chunking.")

        process_text_in_chunks(
            text=prompt,
            voice=args.voice,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
            max_tokens=MAX_TOKENS,
            output_file=output_file
        )
    else:
        # Process the entire text at once
        audio_segments = generate_speech_from_api(
            prompt=prompt,
            voice=args.voice,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
            max_tokens=MAX_TOKENS,
            output_file=output_file
        )

    end_time = time.time()
    print(f"Speech generation completed in {end_time - start_time:.2f} seconds")
    print(f"Audio saved to {output_file}")

if __name__ == "__main__":
    main()