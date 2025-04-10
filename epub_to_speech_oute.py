# --- START OF FILE epub_to_speech_oute.py ---

import os
import sys
import time
# import wave # No longer directly needed here
import numpy as np
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
MODEL_VERSION = outetts.Models.VERSION_1_0_SIZE_1B
MODEL_BACKEND = outetts.Backend.LLAMACPP
MODEL_QUANT = outetts.LlamaCppQuantization.FP16
MODEL_PATH = None # Set if needed

DEFAULT_SPEAKER = "EN-FEMALE-1-NEUTRAL"
SPEAKER_PROFILE_DIR = "speaker_profiles"

try:
    os.makedirs(SPEAKER_PROFILE_DIR, exist_ok=True)
except OSError as e:
    print(f"Warning: Could not create speaker profile directory '{SPEAKER_PROFILE_DIR}': {e}")

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
                quantization=MODEL_QUANT if MODEL_BACKEND == outetts.Backend.LLAMACPP else None
                # Removed 'path=MODEL_PATH' as it's not supported by this outetts version's auto_config
            )
            print(f"Using outeTTS Model Config: {model_config}")
            outeTTS_interface = outetts.Interface(config=model_config)
            print("outeTTS Interface Initialized.")
        except Exception as e:
            print(f"FATAL ERROR: Failed to initialize outeTTS Interface: {e}")
            print("Please ensure the outeTTS library is installed, model files are downloaded,")
            print("and the configuration (MODEL_VERSION, MODEL_BACKEND, etc.) is correct.")
            # Ensure models are placed where outetts expects them if not specifying a path.
            raise RuntimeError(f"outeTTS Initialization Failed: {e}") from e
    return outeTTS_interface

# --- Default Sampler Parameters ---
DEFAULT_TEMPERATURE = 0.75
DEFAULT_REPETITION_PENALTY = 1.1
DEFAULT_TOP_K = 40
DEFAULT_TOP_P = 0.9
DEFAULT_MIN_P = 0.05
DEFAULT_MIROSTAT = False
DEFAULT_MIROSTAT_TAU = 5.0
DEFAULT_MIROSTAT_ETA = 0.1

# Maximum generation length (optional, can be passed in GenerationConfig)
# DEFAULT_MAX_LENGTH = 8192

def ensure_directory_exists(directory):
    """Ensure that a directory exists, create it if it doesn't."""
    if not os.path.exists(directory):
        os.makedirs(directory)

# --- EPUB Handling (Unchanged) ---
def html_to_text(html_content):
    """Convert HTML content to plain text."""
    soup = BeautifulSoup(html_content, 'html.parser')
    # Keep title tags if they exist, might be useful for context/debugging
    # title_text = soup.title.string if soup.title else ""
    for script in soup(["script", "style"]):
        script.extract()
    h = html2text.HTML2Text()
    h.ignore_links = True # Usually don't want URLs read out
    h.ignore_images = True
    h.ignore_tables = False # Keep tables, structure might matter
    h.ignore_emphasis = False # Keep emphasis like *italic* or **bold**
    h.body_width = 0 # Don't wrap lines
    text = h.handle(str(soup))
    # Clean up excessive newlines often resulting from block elements
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove common HTML artifacts like image placeholders if missed
    text = re.sub(r'\[image:.*?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\d+\]', '', text) # Remove footnote numbers like [1]
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
        chapters = []
        items_to_process = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]

        toc_titles = {}
        for item in book.toc:
            title = item.title
            if not title or len(title.strip()) < 2: continue # Allow slightly shorter titles
            if hasattr(item, 'href') and item.href:
                # Normalize href slightly? Maybe remove fragment (#section)
                base_href = item.href.split('#')[0]
                toc_titles[base_href] = title
            elif hasattr(item, 'get_name') and item.get_name() and item.get_name() != getattr(item, 'href', None):
                 toc_titles[item.get_name()] = title

        print(f"Found {len(items_to_process)} potential content documents.")
        extracted_chapters_data = {} # Use href as key to store temporary data

        for i, item in enumerate(items_to_process):
            item_id = item.get_id()
            item_name = item.get_name()
            item_href = None
            if hasattr(item, 'href') and item.href:
                item_href = item.href.split('#')[0] # Use base href for matching
            elif item_name and ('/' in item_name or '.' in item_name):
                item_href = item_name

            if not item_href:
                # print(f"  Skipping item {i+1} ('{item_id}', Name: '{item_name}'): Could not determine a valid path (href/name).")
                continue

            try:
                content = item.get_content().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                item_text = html_to_text(content)

                # Skip items with very little text content
                if len(item_text) < 100: # Adjust threshold if needed
                    # print(f"  Skipping item {i+1} ('{item_href}'): Too short ({len(item_text)} chars)")
                    continue

                # Determine Chapter Title
                chapter_title = toc_titles.get(item_href) # Primary lookup

                # Fallback title logic
                if not chapter_title or len(chapter_title) < 3:
                    title_tag = soup.find(['h1', 'h2', 'h3', 'h4', 'title']) # Added title tag
                    if title_tag:
                        potential_title = title_tag.get_text(strip=True)
                        if potential_title and len(potential_title) > 2:
                            chapter_title = potential_title
                if not chapter_title or len(chapter_title) < 3:
                     # Use filename as last resort, clean it up
                    potential_title = os.path.splitext(os.path.basename(item_name or item_href))[0]
                    potential_title = re.sub(r'[^a-zA-Z0-9\s]', '', potential_title).replace('_', ' ').replace('-', ' ').strip()
                    if potential_title and len(potential_title) > 3 and not potential_title.lower().startswith(("split", "part", "chapter", "ch")):
                        chapter_title = potential_title.title()
                    else:
                        chapter_title = f"Section {len(extracted_chapters_data) + 1}" # Generic fallback

                chapter_title = re.sub(r'\s+', ' ', chapter_title).strip()

                # Store with href as key for ordering later
                extracted_chapters_data[item_href] = {
                    'id': item_id,
                    'title': chapter_title,
                    'content': item_text
                }
                # print(f"  Extracted content for {item_href}: '{chapter_title}' ({len(item_text)} chars)")

            except Exception as item_exc:
                 print(f"  Warning: Could not process content of item {i+1} ('{item_name}', Path: {item_href}): {item_exc}")
                 continue

        print(f"Initial extraction found content for {len(extracted_chapters_data)} items.")
        if not extracted_chapters_data:
             print("Warning: No valid chapter content extracted.")
             return book_title, []

        # --- Order chapters based on the book's spine ---
        ordered_chapters = []
        processed_hrefs = set()
        spine_href_order = []

        try:
            # print("Attempting to process spine for chapter order...")
            for spine_entry in book.spine:
                item_id = None
                item = None
                spine_href = None
                if isinstance(spine_entry, tuple): item_id = spine_entry[0]
                elif isinstance(spine_entry, str): item_id = spine_entry
                if item_id: item = book.get_item_with_id(item_id)

                if item:
                    if hasattr(item, 'href') and item.href:
                        spine_href = item.href.split('#')[0]
                    elif item.get_name() and ('/' in item.get_name() or '.' in item.get_name()):
                        spine_href = item.get_name()

                if spine_href and spine_href in extracted_chapters_data: # Only add if we extracted content
                    spine_href_order.append(spine_href)
                # elif spine_href:
                #     print(f"  Spine item '{spine_href}' found but no content extracted for it.")

            if spine_href_order:
                # print(f"Processing {len(spine_href_order)} items found in spine order with extracted content.")
                for href in spine_href_order:
                    if href not in processed_hrefs:
                        chapter_data = extracted_chapters_data[href]
                        # Add the href back into the dict for potential debugging/reference
                        chapter_data['href'] = href
                        ordered_chapters.append(chapter_data)
                        processed_hrefs.add(href)
                        # print(f"  Ordered chapter from spine: {chapter_data['title']} ({href})")
            else:
                 print("No valid items could be ordered based on the spine content or extracted data.")

        except Exception as spine_exc:
            print(f"Warning: Error processing EPUB spine for ordering: {spine_exc}. Falling back.")
            ordered_chapters = []
            processed_hrefs = set()

        # Append any remaining chapters not covered by the spine (maintains extraction order for these)
        fallback_added = 0
        for href, chapter_data in extracted_chapters_data.items():
            if href not in processed_hrefs:
                 chapter_data['href'] = href # Add href back
                 ordered_chapters.append(chapter_data)
                 processed_hrefs.add(href)
                 fallback_added += 1

        if fallback_added > 0:
             print(f"Appended {fallback_added} chapters not ordered by spine.")

        # Final check if ordering failed completely
        if not ordered_chapters and extracted_chapters_data:
            print("Warning: Spine ordering failed. Using original extracted order (may not be correct).")
            # Convert dict back to list, adding href
            ordered_chapters = [dict(v, href=k) for k, v in extracted_chapters_data.items()]

        final_chapters = ordered_chapters
        print(f"Successfully extracted and ordered {len(final_chapters)} chapters.")
        return book_title, final_chapters

    except Exception as e:
        import traceback
        print(f"Error processing EPUB file '{epub_path}': {e}")
        print(traceback.format_exc())
        return f"Error Reading {os.path.basename(epub_path)}", []


# --- Audio Generation using outeTTS ---

def generate_speech_for_chapter(text, output_file, speaker_profile, sampler_options, log_callback=print):
    """
    Generates speech for a given text (chapter) using outeTTS.
    Accepts speaker_profile as str (name/path) or the direct speaker object.
    Accepts sampler_options as a dictionary.
    """
    active_speaker = None
    try:
        interface = get_outeTTS_interface()
        if not interface:
             raise RuntimeError("outeTTS Interface not available.")

        # --- Determine the speaker object ---
        if not isinstance(speaker_profile, str):
            log_callback("Using pre-loaded/created custom speaker object.")
            active_speaker = speaker_profile
        else:
            speaker_path_or_name = speaker_profile
            log_callback(f"Loading speaker profile: {speaker_path_or_name}")
            try:
                if os.path.exists(speaker_path_or_name) and speaker_path_or_name.lower().endswith(".json"):
                     log_callback(f"  Loading speaker from file: {speaker_path_or_name}")
                     active_speaker = interface.load_speaker(speaker_path_or_name)
                else:
                     if os.path.sep in speaker_path_or_name or speaker_path_or_name.lower().endswith('.json'):
                          log_callback(f"  Warning: Path specified but not found: '{speaker_path_or_name}'. Trying as default name.")
                     log_callback(f"  Attempting to load default/built-in speaker: {speaker_path_or_name}")
                     active_speaker = interface.load_default_speaker(speaker_path_or_name)
            except Exception as speaker_load_err:
                log_callback(f"  WARNING: Failed to load speaker '{speaker_path_or_name}'. Falling back to default '{DEFAULT_SPEAKER}'. Error: {speaker_load_err}")
                try:
                    active_speaker = interface.load_default_speaker(DEFAULT_SPEAKER)
                except Exception as fallback_err:
                     log_callback(f"  FATAL: Failed to load default speaker '{DEFAULT_SPEAKER}'! Error: {fallback_err}")
                     raise RuntimeError(f"Failed to load any speaker profile, including default '{DEFAULT_SPEAKER}'.") from fallback_err

        if active_speaker is None:
             log_callback(f"ERROR: Could not obtain a valid speaker object for profile '{speaker_profile}'. Using default '{DEFAULT_SPEAKER}'.")
             try:
                 active_speaker = interface.load_default_speaker(DEFAULT_SPEAKER)
             except Exception as final_fallback_err:
                 log_callback(f"  FATAL: Final attempt to load default speaker '{DEFAULT_SPEAKER}' failed! Error: {final_fallback_err}")
                 raise RuntimeError(f"Failed to load default speaker profile '{DEFAULT_SPEAKER}' as final fallback.") from final_fallback_err
             if active_speaker is None: # Safety check
                  raise RuntimeError(f"Could not load default speaker '{DEFAULT_SPEAKER}' even as fallback.")

        # --- Create SamplerConfig ---
        log_callback(f"Using sampler config: {sampler_options}")
        try:
            sampler_config = outetts.SamplerConfig(
                temperature=float(sampler_options.get("temperature", DEFAULT_TEMPERATURE)),
                repetition_penalty=float(sampler_options.get("repetition_penalty", DEFAULT_REPETITION_PENALTY)),
                top_k=int(sampler_options.get("top_k", DEFAULT_TOP_K)),
                top_p=float(sampler_options.get("top_p", DEFAULT_TOP_P)),
                min_p=float(sampler_options.get("min_p", DEFAULT_MIN_P)),
                mirostat=bool(sampler_options.get("mirostat", DEFAULT_MIROSTAT)),
                mirostat_tau=float(sampler_options.get("mirostat_tau", DEFAULT_MIROSTAT_TAU)),
                mirostat_eta=float(sampler_options.get("mirostat_eta", DEFAULT_MIROSTAT_ETA)),
            )
        except (ValueError, TypeError) as config_err:
             log_callback(f"  ERROR: Invalid value in sampler_options: {config_err}. Using default sampler config.")
             # Fallback to completely default sampler config on error
             sampler_config = outetts.SamplerConfig(temperature=DEFAULT_TEMPERATURE) # Minimal default


        # --- Generate Speech ---
        log_callback("Generating speech...")
        start_time = time.time()

        gen_config = outetts.GenerationConfig(
            text=text,
            generation_type=outetts.GenerationType.CHUNKED, # Default chunked generation
            speaker=active_speaker,
            sampler_config=sampler_config, # Pass the configured sampler
            # max_length=int(sampler_options.get("max_length", DEFAULT_MAX_LENGTH)) # Optional: Pass max_length if needed
        )

        # Generate audio (assuming generate handles file saving now or returns object)
        # Assuming interface.generate returns an object with a save method
        output_audio = interface.generate(config=gen_config)
        output_audio.save(output_file) # Save the generated audio

        end_time = time.time()
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
        if os.path.exists(output_file):
            try: os.remove(output_file)
            except OSError: pass
        return False


# --- Merging and M4B Conversion (Unchanged) ---

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

    # Sort chapter files numerically based on the leading digits in the filename
    # This ensures correct order if file system listing was weird
    try:
        chapter_files.sort(key=lambda f: int(re.match(r"^\d+", os.path.basename(f)).group(0)) if re.match(r"^\d+", os.path.basename(f)) else float('inf'))
    except Exception as sort_err:
         print(f"Warning: Could not numerically sort chapter files, using provided order. Error: {sort_err}")


    for i, chapter_file in enumerate(chapter_files):
        if not os.path.exists(chapter_file):
            print(f"Warning: Chapter file not found, skipping merge: {chapter_file}")
            continue

        duration = get_audio_duration(chapter_file)
        if duration <= 0.1: # Skip very short/empty files
            print(f"Warning: Chapter file has negligible duration, skipping merge: {chapter_file}")
            continue

        valid_chapter_files.append(chapter_file)
        try:
            base_name = os.path.basename(chapter_file)
            title_part = os.path.splitext(base_name)[0]
            chapter_title = re.sub(r"^\d+_", "", title_part).replace('_', ' ')
        except Exception:
            chapter_title = f"Chapter {i+1}" # Fallback index based on loop

        chapter_info = {
            'index': i, # Use loop index for ffmpeg metadata ordering
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
        # Iterate using valid_chapter_files to ensure we only merge existing ones
        for chapter_file in valid_chapter_files:
             audio = AudioSegment.from_wav(chapter_file)
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
        # Pass chapter_info_list which contains start/end times calculated earlier
        success = convert_wav_to_m4b(output_wav, output_m4b, chapter_info_list, silent)
        if not success:
             print(f"Warning: Failed to create M4B file.")

    return True


def convert_wav_to_m4b(wav_file, output_file, chapter_info_list=None, silent=False):
    """Convert WAV file to M4B with chapter information."""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
        # print("ffmpeg found.") # Less verbose
    except (subprocess.SubprocessError, FileNotFoundError, Exception) as ffmpeg_err:
        print(f"ERROR: ffmpeg command failed: {ffmpeg_err}")
        print("Please ensure ffmpeg is installed and accessible in your system's PATH.")
        return False

    chapters_file = None
    metadata_content = ";FFMETADATA1\n"

    # Add global metadata (optional)
    # book_title = os.path.basename(os.path.splitext(output_file)[0]).replace('_complete', '').replace('_', ' ')
    # metadata_content += f"title={book_title}\n"
    # metadata_content += f"artist=outeTTS Conversion\n"
    # metadata_content += f"album={book_title}\n"
    # metadata_content += "\n"

    if chapter_info_list:
        print(f"Generating chapter metadata for {len(chapter_info_list)} chapters...")
        for chapter in chapter_info_list:
             metadata_content += "[CHAPTER]\n"
             metadata_content += "TIMEBASE=1/1000\n"
             metadata_content += f"START={int(chapter['start_time'] * 1000)}\n"
             metadata_content += f"END={int(chapter['end_time'] * 1000)}\n"
             safe_title = str(chapter['title']).replace('=', '\\=').replace(';', '\\;').replace('#', '\\#').replace('\\', '\\\\').replace('\n', ' ')
             metadata_content += f"title={safe_title}\n\n" # Extra newline between chapters

        chapters_file = os.path.splitext(output_file)[0] + "_ffmpeg_metadata.txt"
        try:
            with open(chapters_file, 'w', encoding='utf-8') as f:
                 f.write(metadata_content)
            print(f"Created chapter metadata file: {chapters_file}")
        except Exception as meta_err:
            print(f"Error writing metadata file: {meta_err}")
            chapters_file = None

    cmd = ["ffmpeg", "-y"]
    cmd.extend(["-i", wav_file])
    if chapters_file and os.path.exists(chapters_file):
        cmd.extend(["-i", chapters_file])

    cmd.extend([
        "-map", "0:a",
        "-c:a", "aac",
        "-b:a", "128k", # Common bitrate for audiobooks
        # "-ac", "1", # Force mono if desired
        # "-ar", "24000", # Match outetts rate if known and desired
        "-movflags", "+faststart" # Good practice for streaming/seeking
    ])

    if chapters_file and os.path.exists(chapters_file):
        cmd.extend(["-map_metadata", "1"])

    cmd.append(output_file)

    print(f"\nExecuting ffmpeg command:")
    # Use subprocess.list2cmdline for safer display on Windows if needed, otherwise join
    try:
        cmd_display = subprocess.list2cmdline(cmd) if os.name == 'nt' else ' '.join(cmd)
    except AttributeError: # Older Python without list2cmdline
        cmd_display = ' '.join(cmd)
    print(f"  {cmd_display}")

    try:
        stderr_pipe = subprocess.PIPE if not silent else subprocess.DEVNULL
        stdout_pipe = subprocess.PIPE if not silent else subprocess.DEVNULL

        process = subprocess.run(cmd, stdout=stdout_pipe, stderr=stderr_pipe, text=True, check=False, encoding='utf-8', errors='replace')

        if process.returncode != 0:
            print(f"ERROR: ffmpeg conversion failed (return code {process.returncode}).")
            if not silent:
                print("--- ffmpeg stdout ---")
                print(process.stdout or "[No stdout]")
                print("--- ffmpeg stderr ---")
                print(process.stderr or "[No stderr]")
            # Attempt cleanup even on failure
            if chapters_file and os.path.exists(chapters_file):
                 try: os.remove(chapters_file)
                 except OSError: pass
            return False
        else:
            print(f"\nSuccessfully converted to M4B: {output_file}")
            if chapters_file and os.path.exists(chapters_file):
                 try: os.remove(chapters_file)
                 except OSError: pass
            return True

    except FileNotFoundError:
         print("ERROR: ffmpeg command not found. Please ensure it's installed and in PATH.")
         return False
    except Exception as e:
        print(f"Error running ffmpeg: {e}")
        # Attempt cleanup on general error
        if chapters_file and os.path.exists(chapters_file):
                try: os.remove(chapters_file)
                except OSError: pass
        return False


# --- Main Processing Logic (Adapted for UI) ---

# Updated function signature to accept sampler_options
def process_epub_chapters(epub_path, output_dir, selected_chapter_indices, speaker_profile, sampler_options, log_callback, progress_callback, processing_chapter_callback, check_stop_callback, overwrite_callback):
    """
    Processes selected chapters of an EPUB using outeTTS.
    Accepts sampler_options dictionary.
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

        effective_output_dir = output_dir
        if not effective_output_dir:
            safe_book_title = re.sub(r'[^\w\s-]', '', book_title).strip().replace(' ', '_')
            effective_output_dir = f"outputs/epub_{safe_book_title}"

        ensure_directory_exists(effective_output_dir)
        log_callback(f"Output directory: {os.path.abspath(effective_output_dir)}")

        chapter_files = []
        for i, (original_index, chapter) in enumerate(selected_chapters_data):
            if check_stop_callback():
                log_callback("Conversion stopped by user.")
                return False, "Stopped"

            log_callback(f"\n▶ Processing chapter {i + 1}/{total_chapters_to_process}: {chapter['title']}")
            processing_chapter_callback(original_index)
            progress_callback(i + 1, total_chapters_to_process, chapter['title'])

            safe_title = re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
            if not safe_title: safe_title = f"chapter_{original_index + 1}"
            output_file = os.path.join(effective_output_dir, f"{original_index + 1:03d}_{safe_title}.wav")

            if os.path.exists(output_file):
                log_callback(f"  WARNING: Chapter WAV file exists: {output_file}. Overwriting.")

            # Generate speech, passing sampler_options
            success = generate_speech_for_chapter(
                text=chapter['content'],
                output_file=output_file,
                speaker_profile=speaker_profile,
                sampler_options=sampler_options, # Pass the dictionary here
                log_callback=lambda msg: log_callback(f"  {msg}")
            )

            if success:
                chapter_files.append(output_file)
                log_callback(f"✓ Chapter {i + 1} completed.")
            else:
                log_callback(f"❌ ERROR processing chapter {i + 1}: {chapter['title']}. Skipping.")

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

        existing_final_files = []
        if os.path.exists(output_wav): existing_final_files.append(os.path.basename(output_wav))
        if os.path.exists(output_m4b): existing_final_files.append(os.path.basename(output_m4b))

        if existing_final_files:
            if not overwrite_callback(output_wav, output_m4b):
                 log_callback("Merging aborted by user (overwrite denied).")
                 # Clean up temp files if user cancels merge? Maybe not, they might want them.
                 return False, "Overwrite denied"
            else:
                 log_callback("Overwrite confirmed by user for final files.")

        merge_success = merge_chapter_wav_files(
            chapter_files,
            output_wav,
            create_m4b=True,
            silent=True # Make ffmpeg quieter in UI mode log
        )

        if merge_success:
            log_callback(f"\n✅ All chapters merged into {os.path.basename(output_wav)} (and .m4b)")
            log_callback("Cleaning up individual chapter WAV files...")
            cleaned_count = 0
            for f in chapter_files:
                 try:
                     os.remove(f)
                     cleaned_count += 1
                 except OSError as e: log_callback(f"  Warning: could not remove {os.path.basename(f)}: {e}")
            log_callback(f"Removed {cleaned_count} chapter files.")
        else:
            log_callback(f"\n❌ Failed to merge chapters or create M4B. Individual chapter files kept.")
            return False, "Merge failed"

        return True, "Completed"

    except Exception as e:
        import traceback
        log_callback(f"\n❌ An unexpected error occurred during EPUB processing: {e}")
        log_callback(traceback.format_exc())
        return False, f"Unexpected error: {e}"


# --- CLI Section ---

def main_cli():
    parser = argparse.ArgumentParser(description="outeTTS EPUB to Audiobook Converter (CLI)")
    parser.add_argument("epub_path", help="Path to the EPUB file.")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="Directory to save output files (default: ./outputs/epub_[Book Title]/)")
    parser.add_argument("--speaker", "-s", default=DEFAULT_SPEAKER,
                        help=f"Speaker profile: Built-in name (e.g., {DEFAULT_SPEAKER}), "
                             f"path to a saved .json in '{SPEAKER_PROFILE_DIR}/', "
                             f"or path to a .wav/.mp3 to create profile from (default: {DEFAULT_SPEAKER})")
    # Sampler CLI args
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help=f"Generation temperature (default: {DEFAULT_TEMPERATURE})")
    parser.add_argument("--rep-penalty", type=float, default=DEFAULT_REPETITION_PENALTY, help=f"Repetition penalty (default: {DEFAULT_REPETITION_PENALTY})")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=f"Top-K sampling (0 to disable) (default: {DEFAULT_TOP_K})")
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P, help=f"Top-P nucleus sampling (0.0-1.0) (default: {DEFAULT_TOP_P})")
    parser.add_argument("--min-p", type=float, default=DEFAULT_MIN_P, help=f"Min-P sampling (0.0-1.0) (default: {DEFAULT_MIN_P})")
    parser.add_argument("--mirostat", action='store_true', default=DEFAULT_MIROSTAT, help=f"Enable Mirostat sampling (default: {DEFAULT_MIROSTAT})")
    parser.add_argument("--mirostat-tau", type=float, default=DEFAULT_MIROSTAT_TAU, help=f"Mirostat Tau (target surprise) (default: {DEFAULT_MIROSTAT_TAU})")
    parser.add_argument("--mirostat-eta", type=float, default=DEFAULT_MIROSTAT_ETA, help=f"Mirostat Eta (learning rate) (default: {DEFAULT_MIROSTAT_ETA})")
    # parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="Max generation length (optional)") # Optional

    args = parser.parse_args()

    # Simple CLI callbacks
    def log_cli(message): print(message)
    def progress_cli(current, total, title): print(f"Progress: Chapter {current}/{total} - {title}")
    def processing_cli(index): print(f"Processing chapter index: {index}")
    def check_stop_cli(): return False # No stop in CLI
    def overwrite_cli(wav, m4b):
        resp = input(f"Output file(s) exist ({os.path.basename(wav)}, {os.path.basename(m4b)}). Overwrite? (y/N): ")
        return resp.lower() == 'y'

    print(f"Starting EPUB processing for: {args.epub_path}")

    # --- Handle speaker argument for CLI ---
    speaker_input = args.speaker
    actual_speaker_profile = None
    interface_cli = None
    try:
        is_audio_file = any(speaker_input.lower().endswith(ext) for ext in ['.wav', '.mp3', '.flac', '.ogg'])
        potential_json_path = os.path.join(SPEAKER_PROFILE_DIR, speaker_input)
        if not potential_json_path.lower().endswith('.json'): potential_json_path += ".json"

        if is_audio_file and os.path.exists(speaker_input):
             print(f"Attempting to create speaker from audio file: {speaker_input}")
             interface_cli = get_outeTTS_interface()
             if not interface_cli: raise RuntimeError("outeTTS interface failed to load for CLI speaker creation.")
             actual_speaker_profile = interface_cli.create_speaker(speaker_input)
             print("Speaker created from audio file (temporary, not saved via CLI).")
        elif os.path.exists(potential_json_path):
             print(f"Using speaker from saved profile: {potential_json_path}")
             actual_speaker_profile = potential_json_path # Pass the path
        else:
             print(f"Using speaker name/path: {speaker_input}")
             actual_speaker_profile = speaker_input # Pass name/invalid path
    except Exception as cli_speaker_err:
         print(f"Error handling speaker argument '{speaker_input}': {cli_speaker_err}")
         print(f"Falling back to default speaker: {DEFAULT_SPEAKER}")
         actual_speaker_profile = DEFAULT_SPEAKER
    # --- End CLI speaker handling ---

    # --- Collect Sampler Options for CLI ---
    sampler_options_cli = {
        "temperature": args.temperature,
        "repetition_penalty": args.rep_penalty,
        "top_k": args.top_k,
        "top_p": args.top_p,
        "min_p": args.min_p,
        "mirostat": args.mirostat,
        "mirostat_tau": args.mirostat_tau,
        "mirostat_eta": args.mirostat_eta,
        # "max_length": args.max_length # Optional
    }
    print("Using Sampler Options:")
    for key, value in sampler_options_cli.items():
        print(f"  {key}: {value}")
    print("-" * 30)


    try:
        _, all_chapters_cli = extract_chapters_from_epub(args.epub_path)
        all_indices = list(range(len(all_chapters_cli))) if all_chapters_cli else []
        if not all_indices:
             print("No chapters found to process.")
             return

        success, message = process_epub_chapters(
            epub_path=args.epub_path,
            output_dir=args.output_dir,
            selected_chapter_indices=all_indices,
            speaker_profile=actual_speaker_profile,
            sampler_options=sampler_options_cli, # Pass CLI options
            log_callback=log_cli,
            progress_callback=progress_cli,
            processing_chapter_callback=processing_cli,
            check_stop_callback=check_stop_cli,
            overwrite_callback=overwrite_cli
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