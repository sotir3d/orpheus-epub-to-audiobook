# --- START OF FILE epub_to_speech_oute.py ---

import os
import sys
import time
import wave
import numpy as np
# import sounddevice as sd # No longer needed for direct playback here
import argparse
import re
from pydub import AudioSegment
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import html2text
import subprocess
from datetime import timedelta
import outetts # Import outetts

# --- outeTTS Configuration ---
# Adjust these paths and settings based on your setup
MODEL_VERSION = outetts.Models.VERSION_1_0_SIZE_1B # Or choose another model
MODEL_BACKEND = outetts.Backend.LLAMACPP          # Use LLAMACPP if using GGUF via LM Studio or directly
MODEL_QUANT = outetts.LlamaCppQuantization.FP16 # Choose appropriate quantization
# Specify the path to your downloaded model file if auto_config can't find it
# Or if you want to ensure a specific file is used.
# Leave as None if outetts should search default paths.
# Example: MODEL_PATH = "/path/to/your/models/gguf-models/outertts-1b-medium-f16.gguf"
MODEL_PATH = None

DEFAULT_SPEAKER = "EN-FEMALE-1-NEUTRAL" # Default built-in speaker

# Initialize outeTTS Interface (globally or lazy-loaded)
# Using lazy loading to avoid slow startup if the script is just imported
outeTTS_interface = None
def get_outeTTS_interface():
    """Initializes and returns the outeTTS interface."""
    global outeTTS_interface
    if outeTTS_interface is None:
        print("Initializing outeTTS Interface...")
        try:
            model_config = outetts.ModelConfig.auto_config(
                model=MODEL_VERSION,
                backend=MODEL_BACKEND,
                # Pass quantization only if backend is LLAMACPP
                quantization=MODEL_QUANT if MODEL_BACKEND == outetts.Backend.LLAMACPP else None,
                # Uncomment and set path if needed
                # path=MODEL_PATH
            )
            print(f"Using outeTTS Model Config: {model_config}")
            outeTTS_interface = outetts.Interface(config=model_config)
            print("outeTTS Interface Initialized.")
        except Exception as e:
            print(f"FATAL ERROR: Failed to initialize outeTTS Interface: {e}")
            print("Please ensure the outeTTS library is installed, model files are downloaded,")
            print("and the configuration (MODEL_VERSION, MODEL_BACKEND, etc.) is correct.")
            # Depending on context, might want to raise or sys.exit
            raise RuntimeError(f"outeTTS Initialization Failed: {e}") from e
    return outeTTS_interface

# Parameters (Temperature is now the main one)
TEMPERATURE = 0.4 # Default temperature for outeTTS

# Note: MAX_CHUNK_LENGTH is no longer needed as outeTTS handles chunking

# Removed Orpheus/LM Studio specific constants and functions
# (API_URL, HEADERS, MAX_TOKENS, TOP_P, REPETITION_PENALTY, AVAILABLE_VOICES, DEFAULT_VOICE)
# (format_prompt, generate_tokens_from_api, turn_token_into_id, convert_to_audio, etc.)

def ensure_directory_exists(directory):
    """Ensure that a directory exists, create it if it doesn't."""
    if not os.path.exists(directory):
        os.makedirs(directory)

# --- EPUB Handling (Unchanged) ---

def html_to_text(html_content):
    """Convert HTML content to plain text."""
    soup = BeautifulSoup(html_content, 'html.parser')
    for script in soup(["script", "style"]):
        script.extract()
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_tables = False
    h.ignore_emphasis = False
    h.body_width = 0
    text = h.handle(str(soup))
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\[.*?\]', '', text)
    return text.strip()

def extract_chapters_from_epub(epub_path):
    """Extract chapters from an EPUB file."""
    try:
        book = epub.read_epub(epub_path)
        book_title = book.get_metadata('DC', 'title')
        if book_title:
            book_title = book_title[0][0]
        else:
            book_title = os.path.basename(epub_path).replace('.epub', '')

        print(f"Processing book: {book_title}")
        chapters = []
        items_to_process = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]

        # Try to get chapter titles from TOC first
        toc_titles = {item.href: item.title for item in book.toc}

        print(f"Found {len(items_to_process)} potential content documents.")

        for i, item in enumerate(items_to_process):
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                item_text = html_to_text(content) # Convert early to check length

                # Basic filtering (adjust thresholds as needed)
                if len(item_text) < 200:
                    # print(f"  Skipping item {i+1} ({item.get_name()}): Too short ({len(item_text)} chars)")
                    continue

                 # Determine Chapter Title
                chapter_title = None
                # 1. Check TOC
                if item.href in toc_titles:
                    chapter_title = toc_titles[item.href]

                # 2. Check for headings in content if TOC didn't provide a good title
                if not chapter_title or chapter_title.lower() == "untitled" or len(chapter_title) < 3:
                    title_tag = soup.find(['h1', 'h2', 'h3', 'h4'])
                    if title_tag:
                        potential_title = title_tag.get_text(strip=True)
                        if len(potential_title) > 2: # Avoid very short headings
                            chapter_title = potential_title


                # 3. Fallback to item name or generic title
                if not chapter_title or chapter_title.lower() == "untitled" or len(chapter_title) < 3:
                    if item.get_name() and len(item.get_name()) > 3:
                         chapter_title = item.get_name()
                    else:
                         chapter_title = f"Chapter {len(chapters) + 1}" # Use current chapter count

                # Clean up title
                chapter_title = chapter_title.strip()
                # Remove excessive whitespace within the title
                chapter_title = re.sub(r'\s+', ' ', chapter_title)

                chapters.append({
                    'id': item.get_id(),
                    'href': item.href,
                    'title': chapter_title,
                    'content': item_text
                })
                print(f"  Extracted chapter {len(chapters)}: '{chapter_title}' ({len(item_text)} chars)")

            except Exception as item_exc:
                 print(f"  Warning: Could not process item {item.get_name()}: {item_exc}")
                 continue # Skip this item


        # Attempt to reorder chapters based on the book's spine
        ordered_chapters = []
        spine_hrefs = [item[0].href for item in book.spine]
        processed_hrefs = set()

        # Add chapters in spine order
        for href in spine_hrefs:
            for chapter in chapters:
                if chapter['href'] == href and href not in processed_hrefs:
                    ordered_chapters.append(chapter)
                    processed_hrefs.add(href)
                    break

        # Add any remaining chapters that weren't in the spine (e.g., appendix, notes)
        for chapter in chapters:
            if chapter['href'] not in processed_hrefs:
                 print(f"  Appending chapter not found in spine: {chapter['title']} ({chapter['href']})")
                 ordered_chapters.append(chapter)
                 processed_hrefs.add(chapter['href']) # Should be added already but ensure


        if not ordered_chapters and chapters:
             print("Warning: Could not order chapters based on spine, using extracted order.")
             ordered_chapters = chapters


        print(f"Successfully extracted {len(ordered_chapters)} chapters.")
        return book_title, ordered_chapters

    except Exception as e:
        import traceback
        print(f"Error processing EPUB file '{epub_path}': {e}")
        print(traceback.format_exc())
        return f"Error Reading {os.path.basename(epub_path)}", []


# --- Audio Generation using outeTTS (Replaces Orpheus API calls) ---

def generate_speech_for_chapter(text, output_file, speaker_profile=DEFAULT_SPEAKER, temperature=TEMPERATURE, log_callback=print):
    """Generates speech for a given text (chapter) using outeTTS."""
    try:
        interface = get_outeTTS_interface() # Initialize if needed
        if not interface:
             raise RuntimeError("outeTTS Interface not available.")

        log_callback(f"Loading speaker profile: {speaker_profile}")
        # TODO: Add logic here to load speaker from file if speaker_profile is a path
        # For now, assumes it's a built-in name like "EN-FEMALE-1-NEUTRAL"
        try:
            if os.path.exists(speaker_profile) and speaker_profile.endswith(".json"):
                 log_callback(f"  Loading speaker from file: {speaker_profile}")
                 speaker = interface.load_speaker(speaker_profile)
            else:
                 log_callback(f"  Loading default/built-in speaker: {speaker_profile}")
                 speaker = interface.load_default_speaker(speaker_profile)
        except Exception as speaker_load_err:
            log_callback(f"  WARNING: Failed to load speaker '{speaker_profile}'. Falling back to default '{DEFAULT_SPEAKER}'. Error: {speaker_load_err}")
            speaker = interface.load_default_speaker(DEFAULT_SPEAKER)


        log_callback(f"Generating speech (temp={temperature})...")
        start_time = time.time()

        gen_config = outetts.GenerationConfig(
            text=text,
            generation_type=outetts.GenerationType.CHUNKED, # Use outeTTS internal chunking
            speaker=speaker,
            sampler_config=outetts.SamplerConfig(
                temperature=temperature
                # Add other sampler config options here if needed/available in outeTTS
            ),
        )

        # Generate audio
        output_audio = interface.generate(config=gen_config)

        # Save audio
        output_audio.save(output_file)

        end_time = time.time()
        duration = get_audio_duration(output_file) # Get actual duration after saving
        log_callback(f"Speech generated and saved to {output_file}")
        log_callback(f"Generation took {end_time - start_time:.2f}s for {duration:.2f}s of audio.")
        return True

    except Exception as e:
        import traceback
        log_callback(f"❌ ERROR generating speech for chapter: {e}")
        log_callback(traceback.format_exc())
        # Ensure output file doesn't exist if generation failed midway
        if os.path.exists(output_file):
            try: os.remove(output_file)
            except OSError: pass
        return False


# --- Merging and M4B Conversion (Largely Unchanged, but TEMP_DIR removed) ---

def get_audio_duration(file_path):
    """Get duration of an audio file in seconds."""
    try:
        audio = AudioSegment.from_file(file_path)
        return len(audio) / 1000.0
    except Exception as e:
        print(f"Warning: Could not get duration for {file_path}: {e}")
        return 0.0

def merge_chapter_wav_files(chapter_files, output_wav, create_m4b=True, silent=False):
    """Merge multiple chapter WAV files into a single WAV and optionally M4B file with chapter markers."""
    print(f"\n{'='*80}")
    print("MERGING ALL CHAPTERS INTO SINGLE AUDIOBOOK")
    print(f"{'='*80}")

    chapter_info_list = []
    current_position = 0.0
    valid_chapter_files = [] # Only include files that actually exist and have duration

    for i, chapter_file in enumerate(chapter_files):
        if not os.path.exists(chapter_file):
            print(f"Warning: Chapter file not found, skipping merge: {chapter_file}")
            continue

        duration = get_audio_duration(chapter_file)
        if duration <= 0.1: # Skip very short/empty files
            print(f"Warning: Chapter file has negligible duration, skipping merge: {chapter_file}")
            continue

        valid_chapter_files.append(chapter_file)
        # Try to extract a clean title from the filename (assuming format like 001_Chapter_Title.wav)
        try:
            base_name = os.path.basename(chapter_file)
            # Remove extension, then remove the leading number and underscore
            title_part = os.path.splitext(base_name)[0]
            chapter_title = re.sub(r"^\d+_", "", title_part).replace('_', ' ') # Replace underscores
        except Exception:
            chapter_title = f"Chapter {i+1}" # Fallback

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
        print(f"Chapter {len(valid_chapter_files)}: '{chapter_title}' - Duration: {timedelta(seconds=duration)}")

    if not valid_chapter_files:
        print("Error: No valid chapter audio files found to merge.")
        return False

    print(f"\nMerging {len(valid_chapter_files)} chapter files...")
    combined = AudioSegment.empty()
    try:
        for chapter_info in chapter_info_list: # Iterate using chapter_info for correct order
             audio = AudioSegment.from_wav(chapter_info['file'])
             combined += audio
    except Exception as merge_err:
         print(f"Error during audio segment merging: {merge_err}")
         return False


    try:
        combined.export(output_wav, format="wav")
        print(f"\nAll chapters merged into WAV file: {output_wav}")
        print(f"Total duration: {timedelta(seconds=len(combined)/1000)}")
    except Exception as export_err:
        print(f"Error exporting merged WAV file: {export_err}")
        return False

    if create_m4b:
        output_m4b = os.path.splitext(output_wav)[0] + ".m4b"
        print(f"\nConverting WAV to M4B with chapters...")
        success = convert_wav_to_m4b(output_wav, output_m4b, chapter_info_list, silent)
        if not success:
             print(f"Warning: Failed to create M4B file.")
             # Keep the WAV file anyway

    return True


def convert_wav_to_m4b(wav_file, output_file, chapter_info_list=None, silent=False):
    """Convert WAV file to M4B with chapter information."""
    # Check if ffmpeg exists first
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
        print("ffmpeg found.")
    except (subprocess.SubprocessError, FileNotFoundError, Exception) as ffmpeg_err:
        print(f"ERROR: ffmpeg command failed: {ffmpeg_err}")
        print("Please ensure ffmpeg is installed and accessible in your system's PATH.")
        return False # Indicate failure


    chapters_file = None
    metadata_content = ";FFMETADATA1\n" # Start metadata content

    # Add global metadata (optional, example)
    # metadata_content += f"title={os.path.basename(os.path.splitext(output_file)[0])}\n"
    # metadata_content += f"artist=outeTTS\n"
    # metadata_content += f"album={os.path.basename(os.path.splitext(output_file)[0])}\n"
    # metadata_content += "\n"


    if chapter_info_list:
        print(f"Generating chapter metadata for {len(chapter_info_list)} chapters...")
        for chapter in chapter_info_list:
             metadata_content += "[CHAPTER]\n"
             metadata_content += "TIMEBASE=1/1000\n" # Millisecond precision
             metadata_content += f"START={int(chapter['start_time'] * 1000)}\n"
             metadata_content += f"END={int(chapter['end_time'] * 1000)}\n"
             # Escape special characters for ffmpeg metadata
             safe_title = str(chapter['title']).replace('=', '\\=').replace(';', '\\;').replace('#', '\\#').replace('\\', '\\\\').replace('\n', ' ')
             metadata_content += f"title={safe_title}\n\n"

        chapters_file = os.path.splitext(output_file)[0] + "_ffmpeg_metadata.txt"
        try:
            with open(chapters_file, 'w', encoding='utf-8') as f:
                 f.write(metadata_content)
            print(f"Created chapter metadata file: {chapters_file}")
        except Exception as meta_err:
            print(f"Error writing metadata file: {meta_err}")
            chapters_file = None # Prevent ffmpeg from trying to use a bad file
            # Continue without chapters if metadata fails

    cmd = ["ffmpeg", "-y"] # Overwrite output without asking

    # Input WAV
    cmd.extend(["-i", wav_file])

    # Input Metadata file (if created)
    if chapters_file and os.path.exists(chapters_file):
        cmd.extend(["-i", chapters_file])

    # Output settings
    cmd.extend([
        "-map", "0:a",              # Map audio from first input (wav)
        "-c:a", "aac",              # Codec
        "-b:a", "128k",             # Bitrate (adjust as needed)
        # "-vn",                    # No video
        # "-ac", "1",               # Force mono (optional)
        # "-ar", "24000",           # Force sample rate (optional, match outetts if needed)
    ])

    # Map metadata if file exists
    if chapters_file and os.path.exists(chapters_file):
        cmd.extend(["-map_metadata", "1"]) # Map metadata from second input (txt file)

    # Output file
    cmd.append(output_file)

    print(f"\nExecuting ffmpeg command:")
    print(f"  {' '.join(cmd)}") # Use print for clarity, especially with escaping

    try:
        process = subprocess.run(cmd, capture_output=not silent, text=True, check=False) # check=False to print stderr

        if process.returncode != 0:
            print(f"ERROR: ffmpeg conversion failed (return code {process.returncode}).")
            if not silent:
                print("--- ffmpeg stdout ---")
                print(process.stdout)
                print("--- ffmpeg stderr ---")
                print(process.stderr)
            return False # Indicate failure
        else:
            print(f"\nSuccessfully converted to M4B: {output_file}")
            # Clean up metadata file
            if chapters_file and os.path.exists(chapters_file):
                 try: os.remove(chapters_file)
                 except OSError: pass
            return True # Indicate success

    except Exception as e:
        print(f"Error running ffmpeg: {e}")
        return False # Indicate failure


# --- Main Processing Logic (Adapted for UI) ---

# This function is called by the UI worker thread
def process_epub_chapters(epub_path, output_dir, temperature, selected_chapter_indices, speaker_profile, log_callback, progress_callback, processing_chapter_callback, check_stop_callback, overwrite_callback):
    """
    Processes selected chapters of an EPUB using outeTTS.
    Designed to be called from a separate thread (like the UI's ConversionWorker).
    """
    try:
        log_callback("Extracting chapters from EPUB...")
        book_title, all_chapters = extract_chapters_from_epub(epub_path)
        if not all_chapters:
            log_callback("❌ Error: No chapters found in EPUB file.")
            return False, "No chapters found"

        selected_chapters_data = [(idx, all_chapters[idx]) for idx in selected_chapter_indices if 0 <= idx < len(all_chapters)]
        total_chapters_to_process = len(selected_chapters_data)

        log_callback(f"Starting conversion of {total_chapters_to_process} chapters for '{book_title}'...")

        # Determine effective output directory
        effective_output_dir = output_dir
        if not effective_output_dir:
            safe_book_title = re.sub(r'[^\w\s-]', '', book_title).strip().replace(' ', '_')
            effective_output_dir = f"outputs/epub_{safe_book_title}"

        ensure_directory_exists(effective_output_dir)
        log_callback(f"Output directory: {os.path.abspath(effective_output_dir)}")

        chapter_files = []
        for i, (original_index, chapter) in enumerate(selected_chapters_data):
            # Check if stop requested before processing each chapter
            if check_stop_callback():
                log_callback("Conversion stopped by user.")
                return False, "Stopped"

            log_callback(f"\n▶ Processing chapter {i + 1}/{total_chapters_to_process}: {chapter['title']}")
            processing_chapter_callback(original_index) # Signal UI to highlight this chapter index
            progress_callback(i + 1, total_chapters_to_process, chapter['title']) # Update progress bar

            safe_title = re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
            if not safe_title: safe_title = f"chapter_{original_index + 1}" # Handle empty titles
            output_file = os.path.join(effective_output_dir, f"{original_index + 1:03d}_{safe_title}.wav")

            # Check for existing file BEFORE generation
            if os.path.exists(output_file):
                log_callback(f"  WARNING: Chapter WAV file exists: {output_file}. Overwriting.")
                # Simple overwrite policy for now. Could add prompt later if needed.

            # Generate speech for this chapter using outeTTS
            success = generate_speech_for_chapter(
                text=chapter['content'],
                output_file=output_file,
                speaker_profile=speaker_profile,
                temperature=temperature,
                log_callback=lambda msg: log_callback(f"  {msg}") # Indent logs
            )

            if success:
                chapter_files.append(output_file)
                log_callback(f"✓ Chapter {i + 1} completed.")
            else:
                log_callback(f"❌ ERROR processing chapter {i + 1}: {chapter['title']}. Skipping.")
                # Continue with the next chapter

        # --- Merging ---
        if check_stop_callback():
            log_callback("Conversion stopped before merging.")
            return False, "Stopped"

        if not chapter_files:
            log_callback("\nNo chapters were processed successfully, skipping merge.")
            return False, "No chapters processed"

        log_callback("\nMerging chapters into final audiobook...")
        safe_book_title = re.sub(r'[^\w\s-]', '', book_title).strip().replace(' ', '_')
        output_wav = os.path.join(effective_output_dir, f"{safe_book_title}_complete.wav")
        output_m4b = os.path.splitext(output_wav)[0] + ".m4b"

        # --- Overwrite Check for Final Files ---
        existing_final_files = []
        if os.path.exists(output_wav): existing_final_files.append(os.path.basename(output_wav))
        if os.path.exists(output_m4b): existing_final_files.append(os.path.basename(output_m4b))

        if existing_final_files:
            if not overwrite_callback(output_wav, output_m4b): # Ask UI thread
                 log_callback("Merging aborted by user (overwrite denied).")
                 # Optional: Clean up generated chapter files?
                 # for f in chapter_files:
                 #     try: os.remove(f)
                 #     except OSError: pass
                 return False, "Overwrite denied" # Treat as stopped/cancelled
            else:
                 log_callback("Overwrite confirmed by user for final files.")


        # Perform the merge
        merge_success = merge_chapter_wav_files(
            chapter_files,
            output_wav,
            create_m4b=True,
            silent=False # Show ffmpeg output in console/log
        )

        if merge_success:
            log_callback(f"\n✅ All chapters merged into {output_wav} (and .m4b)")
            # Optional: Clean up individual chapter WAVs after successful merge?
            # log_callback("Cleaning up individual chapter WAV files...")
            # for f in chapter_files:
            #      try: os.remove(f)
            #      except OSError as e: log_callback(f"  Warning: could not remove {f}: {e}")
        else:
            log_callback(f"\n❌ Failed to merge chapters or create M4B. Individual chapter files kept.")
            return False, "Merge failed" # Indicate error but keep chapters

        return True, "Completed" # Success

    except Exception as e:
        import traceback
        log_callback(f"\n❌ An unexpected error occurred during EPUB processing: {e}")
        log_callback(traceback.format_exc())
        return False, f"Unexpected error: {e}" # Failure


# --- CLI Section (Optional - kept for standalone use) ---

def main_cli():
    parser = argparse.ArgumentParser(description="outeTTS EPUB to Audiobook Converter")
    parser.add_argument("epub_path", help="Path to the EPUB file")
    parser.add_argument("--output-dir", "-o", help="Directory to save output files (default: outputs/epub_[Book Title])")
    parser.add_argument("--speaker", "-s", default=DEFAULT_SPEAKER, help=f"Speaker profile name or path to .json (default: {DEFAULT_SPEAKER})")
    parser.add_argument("--temperature", "-t", type=float, default=TEMPERATURE, help=f"Generation temperature (default: {TEMPERATURE})")
    # Add args for outetts config if needed, e.g., --model-path, --backend

    args = parser.parse_args()

    # Simple wrapper to mimic UI callbacks for CLI use
    stop_requested = False
    def check_stop(): return stop_requested
    def progress(curr, total, title): print(f"Progress: {curr}/{total} - {title}")
    def processing(idx): print(f"Processing chapter index: {idx}")
    def log(msg): print(msg)
    def overwrite(wav, m4b):
        reply = input(f"Output files '{os.path.basename(wav)}' / '{os.path.basename(m4b)}' exist. Overwrite? (y/N): ")
        return reply.lower() == 'y'

    print(f"Starting EPUB processing for: {args.epub_path}")
    print(f"Speaker: {args.speaker}, Temperature: {args.temperature}")

    try:
        success, message = process_epub_chapters(
            epub_path=args.epub_path,
            output_dir=args.output_dir,
            temperature=args.temperature,
            selected_chapter_indices=None, # Process all chapters in CLI mode
            speaker_profile=args.speaker,
            log_callback=log,
            progress_callback=progress,
            processing_chapter_callback=processing,
            check_stop_callback=check_stop, # Basic stop handling not implemented for CLI
            overwrite_callback=overwrite
        )

        print(f"\nProcessing finished. Status: {message}")

    except KeyboardInterrupt:
         print("\nOperation interrupted by user.")
         # stop_requested = True # Might not be checked depending on where it was interrupted
    except Exception as e:
         print(f"\nAn error occurred: {e}")
         import traceback
         traceback.print_exc()


if __name__ == "__main__":
    # This allows running the script standalone from CLI
    main_cli()

# --- END OF FILE epub_to_speech_oute.py ---