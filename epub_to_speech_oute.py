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
SPEAKER_PROFILE_DIR = "speaker_profiles" # Define the directory name

# Ensure speaker profile directory exists on import/startup
try:
    os.makedirs(SPEAKER_PROFILE_DIR, exist_ok=True)
except OSError as e:
    print(f"Warning: Could not create speaker profile directory '{SPEAKER_PROFILE_DIR}': {e}")

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
    """Extract chapters from an EPUB file, trying multiple ways to get item paths."""
    try:
        book = epub.read_epub(epub_path)
        book_title = book.get_metadata('DC', 'title')
        if book_title:
            book_title = book_title[0][0]
        else:
            book_title = os.path.basename(epub_path).replace('.epub', '')

        print(f"Processing book: {book_title}")
        chapters = [] # Temporarily store all extracted chapters here
        items_to_process = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]

        # Create a mapping from various identifiers (href, name, filename) to TOC titles
        toc_titles = {}
        for item in book.toc:
            title = item.title
            if not title or len(title.strip()) < 3:
                 continue # Skip empty or very short TOC titles
            # Store title against potential keys
            if hasattr(item, 'href') and item.href:
                toc_titles[item.href] = title
            # Sometimes TOC might use other identifiers, try adding name if different
            if hasattr(item, 'get_name') and item.get_name() and item.get_name() != getattr(item, 'href', None):
                 toc_titles[item.get_name()] = title


        print(f"Found {len(items_to_process)} potential content documents.")

        # --- First Pass: Extract all potential chapter content and metadata ---
        for i, item in enumerate(items_to_process):
            item_id = item.get_id()
            item_name = item.get_name() # Often same as href or file_name
            item_file_name = getattr(item, 'file_name', None) # Use getattr for safety

            # --- Try harder to find a usable path (href/name) ---
            item_href = None
            if hasattr(item, 'href') and item.href:
                item_href = item.href
            elif item_name: # Fallback to get_name()
                print(f"  Info: Item {i+1} ('{item_id}') missing 'href', trying get_name(): '{item_name}'")
                # Basic check if name looks like a path
                if '/' in item_name or '.' in item_name:
                     item_href = item_name
                # else: # Commented out: file_name is often redundant with get_name
                #    if item_file_name:
                #        print(f"  Info: Item {i+1} ('{item_id}') get_name() unsuitable, trying file_name: '{item_file_name}'")
                #        if '/' in item_file_name or '.' in item_file_name:
                #             item_href = item_file_name

            if not item_href:
                print(f"  Skipping item {i+1} ('{item_id}', Name: '{item_name}'): Could not determine a valid path (href/name).")
                # You could print dir(item) here for deep debugging if needed:
                # print(f"Available attributes for skipped item: {dir(item)}")
                continue
            # --- End Path Finding ---

            try:
                content = item.get_content().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                item_text = html_to_text(content)

                if len(item_text) < 100:
                    # print(f"  Skipping item {i+1} ('{item_name}', Path: {item_href}): Too short ({len(item_text)} chars)")
                    continue

                # Determine Chapter Title
                chapter_title = None
                # 1. Check TOC using the determined item_href or item_name
                if item_href in toc_titles:
                    chapter_title = toc_titles[item_href]
                elif item_name in toc_titles: # Check name as well
                     chapter_title = toc_titles[item_name]

                # 2. Check for headings in content if TOC didn't provide a good title
                if not chapter_title or len(chapter_title) < 3:
                    title_tag = soup.find(['h1', 'h2', 'h3', 'h4'])
                    if title_tag:
                        potential_title = title_tag.get_text(strip=True)
                        if potential_title and len(potential_title) > 2:
                            chapter_title = potential_title

                # 3. Fallback
                if not chapter_title or len(chapter_title) < 3:
                    potential_title = os.path.splitext(os.path.basename(item_name))[0]
                    potential_title = potential_title.replace('_', ' ').replace('-', ' ').strip()
                    if potential_title and len(potential_title) > 3 and not potential_title.lower().startswith("split") and not potential_title.lower().startswith("part"):
                         chapter_title = potential_title.title()
                    else:
                         chapter_title = f"Chapter {len(chapters) + 1}"

                chapter_title = re.sub(r'\s+', ' ', chapter_title).strip()

                # Append chapter data using the determined path
                chapters.append({
                    'id': item_id,
                    'href': item_href, # Store the path we actually found
                    'title': chapter_title,
                    'content': item_text
                })
                # print(f"  Extracted potential chapter {len(chapters)}: '{chapter_title}' ({len(item_text)} chars, Path: {item_href})")

            except Exception as item_exc:
                 print(f"  Warning: Could not process content of item {i+1} ('{item_name}', Path: {item_href}): {item_exc}")
                 continue # Skip this item's content processing

        print(f"Initial extraction found {len(chapters)} potential chapters.")
        if not chapters:
             print("Warning: No valid chapter content extracted in the first pass.")
             return book_title, []

        # --- Second Pass: Attempt to order chapters based on the book's spine ---
        ordered_chapters = []
        processed_hrefs = set()

        try:
            print("Attempting to process spine for chapter order...")
            spine_path_order = [] # Store paths (href or name) from spine
            for spine_entry in book.spine:
                item_id = None
                item = None
                spine_path = None

                if isinstance(spine_entry, tuple) and len(spine_entry) > 0: item_id = spine_entry[0]
                elif isinstance(spine_entry, str): item_id = spine_entry

                if isinstance(item_id, str): item = book.get_item_with_id(item_id)

                if item:
                    # Apply the same path finding logic to the item from the spine
                    if hasattr(item, 'href') and item.href:
                        spine_path = item.href
                    elif item.get_name():
                         # Check if name looks like a path
                         if '/' in item.get_name() or '.' in item.get_name():
                             spine_path = item.get_name()

                if spine_path:
                    spine_path_order.append(spine_path)
                    # print(f"  Spine item found: ID='{item_id}', Path='{spine_path}'")
                # elif item_id:
                    # print(f"  Warning: Could not find item or usable path for spine ID '{item_id}'")


            if spine_path_order:
                print(f"Processing {len(spine_path_order)} items found in spine order.")
                # Add chapters matching the spine order (using the determined 'href' stored in chapter dict)
                for path in spine_path_order:
                    found_chapter = None
                    for chapter in chapters:
                        if chapter['href'] == path: # Match by the stored path
                            found_chapter = chapter
                            break

                    if found_chapter and found_chapter['href'] not in processed_hrefs:
                        ordered_chapters.append(found_chapter)
                        processed_hrefs.add(found_chapter['href'])
                        # print(f"  Ordered chapter from spine: {found_chapter['title']} ({path})")
                    # else:
                        # print(f"  Chapter with path '{path}' from spine not found or already processed.")
            else:
                 print("No valid items could be ordered based on the spine content.")

        except Exception as spine_exc:
            print(f"Warning: Error processing EPUB spine for ordering: {spine_exc}. Falling back.")
            ordered_chapters = []
            processed_hrefs = set()

        # --- Fallback / Append Remaining ---
        initial_ordered_count = len(ordered_chapters)
        for chapter in chapters:
            if chapter['href'] not in processed_hrefs:
                 ordered_chapters.append(chapter)
                 processed_hrefs.add(chapter['href'])

        # Final checks and logging (same as before)
        if not ordered_chapters and chapters:
             print("Warning: Spine ordering failed or produced no results. Using original extracted order.")
             ordered_chapters = chapters
        elif len(ordered_chapters) > initial_ordered_count:
             print(f"Appended {len(ordered_chapters) - initial_ordered_count} chapters not ordered by spine.")
        elif len(ordered_chapters) < len(chapters):
             print(f"Warning: Final chapter count ({len(ordered_chapters)}) is less than initial extraction count ({len(chapters)}). Some chapters might be missing.")
        elif len(ordered_chapters) == len(chapters) and initial_ordered_count == 0:
             print("Used original extracted order (spine processing yielded no order).")


        final_chapters = ordered_chapters
        print(f"Successfully extracted and ordered {len(final_chapters)} chapters.")
        return book_title, final_chapters

    except Exception as e:
        import traceback
        print(f"Error processing EPUB file '{epub_path}': {e}")
        print(traceback.format_exc())
        return f"Error Reading {os.path.basename(epub_path)}", []


# --- Audio Generation using outeTTS (Replaces Orpheus API calls) ---

def generate_speech_for_chapter(text, output_file, speaker_profile, temperature=TEMPERATURE, log_callback=print):
    """
    Generates speech for a given text (chapter) using outeTTS.
    Accepts speaker_profile as str (name/path) or the direct speaker object.
    """
    active_speaker = None # Variable to hold the final Speaker object
    try:
        interface = get_outeTTS_interface()
        if not interface:
             raise RuntimeError("outeTTS Interface not available.")

        # --- Determine the speaker object to use ---
        # If it's not a string, assume it's the speaker object itself
        if not isinstance(speaker_profile, str):
            log_callback("Using pre-loaded/created custom speaker object.")
            active_speaker = speaker_profile
        # Otherwise, treat it as a string (name or path)
        else:
            speaker_path_or_name = speaker_profile
            log_callback(f"Loading speaker profile: {speaker_path_or_name}")
            try:
                # Check if it's a path to an existing JSON file
                if os.path.exists(speaker_path_or_name) and speaker_path_or_name.lower().endswith(".json"):
                     log_callback(f"  Loading speaker from file: {speaker_path_or_name}")
                     active_speaker = interface.load_speaker(speaker_path_or_name)
                # Otherwise, assume it's a built-in default name
                else:
                     # Add a check if the name looks like a path but doesn't exist, maybe warn
                     if os.path.sep in speaker_path_or_name or speaker_path_or_name.lower().endswith('.json'):
                          log_callback(f"  Warning: Path specified but not found: '{speaker_path_or_name}'. Trying as default name.")

                     log_callback(f"  Attempting to load default/built-in speaker: {speaker_path_or_name}")
                     active_speaker = interface.load_default_speaker(speaker_path_or_name)

            except Exception as speaker_load_err:
                log_callback(f"  WARNING: Failed to load speaker '{speaker_path_or_name}'. Falling back to default '{DEFAULT_SPEAKER}'. Error: {speaker_load_err}")
                try:
                    # Explicitly load the guaranteed default name on error
                    active_speaker = interface.load_default_speaker(DEFAULT_SPEAKER)
                except Exception as fallback_err:
                     log_callback(f"  FATAL: Failed to load even the default speaker '{DEFAULT_SPEAKER}'! Error: {fallback_err}")
                     raise RuntimeError(f"Failed to load any speaker profile, including default '{DEFAULT_SPEAKER}'.") from fallback_err

        # --- Ensure speaker was successfully loaded/obtained ---
        if active_speaker is None: # Check if None after all attempts
             log_callback(f"ERROR: Could not obtain a valid speaker object for profile '{speaker_profile}'. Using default.")
             # Final attempt to load default before failing hard
             try:
                 active_speaker = interface.load_default_speaker(DEFAULT_SPEAKER)
             except Exception as final_fallback_err:
                 log_callback(f"  FATAL: Final attempt to load default speaker '{DEFAULT_SPEAKER}' failed! Error: {final_fallback_err}")
                 raise RuntimeError(f"Failed to load default speaker profile '{DEFAULT_SPEAKER}' as final fallback.") from final_fallback_err
             if active_speaker is None: # Should not happen if load_default_speaker doesn't raise but returns None
                  raise RuntimeError(f"Could not load default speaker '{DEFAULT_SPEAKER}' even as fallback.")


        # --- Generate Speech ---
        log_callback(f"Generating speech (temp={temperature})...")
        start_time = time.time()

        gen_config = outetts.GenerationConfig(
            text=text,
            generation_type=outetts.GenerationType.CHUNKED,
            speaker=active_speaker, # Use the determined speaker object
            sampler_config=outetts.SamplerConfig(
                temperature=temperature
            ),
        )

        output_audio = interface.generate(config=gen_config)
        output_audio.save(output_file)

        end_time = time.time()
        # Use a try-except for duration calculation as it might fail on corrupted files
        try:
             duration = get_audio_duration(output_file)
             log_callback(f"Speech generated and saved to {output_file}")
             log_callback(f"Generation took {end_time - start_time:.2f}s for {duration:.2f}s of audio.")
        except Exception as duration_err:
             log_callback(f"Speech generated and saved to {output_file} (could not get duration: {duration_err})")
             log_callback(f"Generation took {end_time - start_time:.2f}s.")

        return True

    except Exception as e:
        import traceback
        log_callback(f"❌ ERROR generating speech for chapter: {e}")
        log_callback(traceback.format_exc())
        # Try to clean up potentially incomplete output file
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
    parser = argparse.ArgumentParser(description="outeTTS EPUB to Audiobook Converter (CLI)")
    # ... (keep epub_path, output-dir args) ...
    parser.add_argument("--speaker", "-s", default=DEFAULT_SPEAKER,
                        help=f"Speaker profile: Built-in name (e.g., {DEFAULT_SPEAKER}), "
                             f"path to a saved .json in '{SPEAKER_PROFILE_DIR}/', "
                             f"or path to a .wav/.mp3 to create profile from (default: {DEFAULT_SPEAKER})")
    # ... (keep temperature arg) ...

    args = parser.parse_args()

    # ... (keep callback setup) ...
    print(f"Starting EPUB processing for: {args.epub_path}")
    print(f"Temperature: {args.temperature}")

    # --- Handle speaker argument for CLI ---
    speaker_input = args.speaker
    actual_speaker_profile = None
    interface_cli = None

    try:
        is_audio_file = any(speaker_input.lower().endswith(ext) for ext in ['.wav', '.mp3', '.flac', '.ogg'])
        potential_json_path = os.path.join(SPEAKER_PROFILE_DIR, speaker_input)
        if not potential_json_path.lower().endswith('.json'):
            potential_json_path += ".json" # Allow providing name without extension

        if is_audio_file and os.path.exists(speaker_input):
             print(f"Attempting to create speaker from audio file: {speaker_input}")
             interface_cli = get_outeTTS_interface()
             if not interface_cli: raise RuntimeError("outeTTS interface failed to load for CLI speaker creation.")
             actual_speaker_profile = interface_cli.create_speaker(speaker_input)
             print("Speaker created from audio file (temporary, not saved via CLI).")
             # NOTE: CLI doesn't automatically save the created profile currently.
        elif os.path.exists(potential_json_path):
             print(f"Using speaker from saved profile: {potential_json_path}")
             actual_speaker_profile = potential_json_path # Pass the path
        else:
             # Assume it's a built-in name or invalid path (generate_speech will handle/fallback)
             print(f"Using speaker name/path: {speaker_input}")
             actual_speaker_profile = speaker_input

    except Exception as cli_speaker_err:
         print(f"Error handling speaker argument '{speaker_input}': {cli_speaker_err}")
         print(f"Falling back to default speaker: {DEFAULT_SPEAKER}")
         actual_speaker_profile = DEFAULT_SPEAKER
    # --- End CLI speaker handling ---


    try:
        # Get all chapter indices for CLI mode
        _, all_chapters_cli = extract_chapters_from_epub(args.epub_path)
        all_indices = list(range(len(all_chapters_cli))) if all_chapters_cli else []

        if not all_indices:
             print("No chapters found to process.")
             return

        success, message = process_epub_chapters(
            epub_path=args.epub_path,
            output_dir=args.output_dir,
            temperature=args.temperature,
            selected_chapter_indices=all_indices, # Process all
            speaker_profile=actual_speaker_profile, # Pass created object or name/path
            log_callback=log,
            progress_callback=progress,
            processing_chapter_callback=processing,
            check_stop_callback=check_stop,
            overwrite_callback=overwrite
        )

        print(f"\nProcessing finished. Status: {message}")

    except KeyboardInterrupt:
         print("\nOperation interrupted by user.")
    except Exception as e:
         print(f"\nAn error occurred: {e}")
         import traceback
         traceback.print_exc()


if __name__ == "__main__":
    main_cli()

# --- END OF FILE epub_to_speech_oute.py ---
