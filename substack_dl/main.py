import os
import requests
from bs4 import BeautifulSoup
from readability import Document
from markdownify import markdownify as md
import time
import yaml
from slugify import slugify
import json
from datetime import datetime
import mimetypes
from urllib.parse import urljoin, urlparse
import argparse
import logging
import pypandoc

# --- Default Configuration Values ---
DEFAULT_CONFIG = {
    "substack_urls": [],
    "formats": ["md", "json"],
    "output_dir": "substack_posts",
    "download_images": True,
    "incremental": False,
    "delay": 1.0,
    "assets_dir_name": "assets"
}
CONFIG_FILE_NAME = "config.yaml" # Default config file name

def load_config(config_path):
    """Loads configuration from a YAML file."""
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            try:
                user_config = yaml.safe_load(f)
                if user_config:
                    return {**DEFAULT_CONFIG, **user_config}
            except yaml.YAMLError as e:
                logging.error(f"Error parsing config file {config_path}: {e}")
    return DEFAULT_CONFIG

def init_argparse(config):
    """Initializes argparse, using loaded config values as defaults."""
    parser = argparse.ArgumentParser(description="Download posts from one or more Substacks.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # If a URL is given via CLI, it overrides the list from config.
    # If no URL via CLI, and only one in config, that one is used.
    # If no URL via CLI, and multiple in config, all are processed.
    # If no URL via CLI and none in config, it's an error (unless we change --url to not be required).
    parser.add_argument(
        "-u", "--url",
        metavar="SUBSTACK_URL",
        help="URL of a single Substack to download. If provided, this overrides any URLs in the config file."
    )
    parser.add_argument(
        "-c", "--config",
        metavar="PATH",
        default=CONFIG_FILE_NAME,
        help=(
            f"Path to a YAML configuration file. If not specified, looks for '{CONFIG_FILE_NAME}' "
            "in the current directory. CLI arguments override config file settings."
        )
    )
    parser.add_argument(
        "-f", "--formats",
        metavar="FMT1,FMT2",
        type=str,
        default=",".join(config.get("formats", DEFAULT_CONFIG["formats"])),
        help="Comma-separated list of output formats (e.g., md,html,json,pdf,epub). Available: md, html, json, pdf, epub."
    )
    parser.add_argument(
        "-o", "--output-dir",
        metavar="DIRECTORY",
        default=config.get("output_dir", DEFAULT_CONFIG["output_dir"]),
        help="Directory where posts will be saved. If multiple Substacks are processed, subdirectories will be created for each."
    )
    parser.add_argument(
        "-i", "--download-images",
        action=argparse.BooleanOptionalAction,
        default=config.get("download_images", DEFAULT_CONFIG["download_images"]),
        help="Download images and rewrite links to local paths. Use --no-download-images to disable."
    )
    parser.add_argument(
        "--incremental",
        action=argparse.BooleanOptionalAction,
        default=config.get("incremental", DEFAULT_CONFIG["incremental"]),
        help="Enable incremental downloads. Skips posts already present in the .download_log.json for that Substack."
    )
    parser.add_argument(
        "-d", "--delay",
        metavar="SECONDS",
        type=float,
        default=float(config.get("delay", DEFAULT_CONFIG["delay"])),
        help="Delay in seconds between network requests to avoid rate limiting."
    )
    parser.add_argument(
        "--assets-dir-name",
        metavar="NAME",
        default=config.get("assets_dir_name", DEFAULT_CONFIG["assets_dir_name"]),
        help="Name of the subdirectory within the output directory (or each Substack's directory) for storing downloaded image assets."
    )
    # Adding a general note about config file precedence might be good in the description or epilog.
    # For now, the --config help text mentions it.

    args = parser.parse_args()

    # Post-processing for URLs:
    # If args.url is provided, it takes precedence and becomes a single-item list.
    # Otherwise, use substack_urls from the config.
    if args.url:
        args.substack_urls = [args.url]
    else:
        args.substack_urls = config.get("substack_urls", [])

    if not args.substack_urls:
        parser.error("No Substack URL provided. Please specify a URL via --url or in the config file's 'substack_urls' list.")

    return args

def extract_metadata_from_post(post_html, post_url):
    """
    Extracts metadata (author, pub_date, tags) from the post's HTML content.
    Primarily looks for JSON-LD, then falls back to meta tags or other elements.
    """
    soup = BeautifulSoup(post_html, 'html.parser')
    metadata = {
        "title": None, # Title will be extracted by readability later
        "author": None,
        "published_date": None,
        "tags": [],
        "url": post_url
    }

    # Try to find JSON-LD schema
    json_ld_script = soup.find('script', type='application/ld+json')
    if json_ld_script:
        try:
            json_data_list = json.loads(json_ld_script.string)
            # Ensure json_data is a dictionary, taking the first relevant item if it's a list
            if isinstance(json_data_list, list):
                processed_json_data = {}
                for item in json_data_list:
                    if isinstance(item, dict) and (item.get('@type') == 'NewsArticle' or item.get('@type') == 'Article'):
                        processed_json_data = item # Prioritize NewsArticle or Article
                        break
                if not processed_json_data and json_data_list and isinstance(json_data_list[0], dict):
                    processed_json_data = json_data_list[0] # Fallback to the first dict if no specific type found
                json_data = processed_json_data
            elif isinstance(json_data_list, dict): # If it's already a dictionary
                json_data = json_data_list
            else:
                json_data = {}


            if json_data: # Check if json_data is not empty
                metadata["title"] = json_data.get('headline')
                author_data = json_data.get('author')
                if isinstance(author_data, dict):
                    metadata["author"] = author_data.get('name')
                elif isinstance(author_data, list) and len(author_data) > 0 and isinstance(author_data[0], dict):
                     metadata["author"] = author_data[0].get('name')

                metadata["published_date"] = json_data.get('datePublished')
                keywords = json_data.get('keywords')
                if isinstance(keywords, str):
                    metadata["tags"] = [tag.strip() for tag in keywords.split(',') if tag.strip()]
                elif isinstance(keywords, list):
                    metadata["tags"] = [tag for tag in keywords if isinstance(tag, str) and tag.strip()]
        except json.JSONDecodeError:
            logging.warning(f"Could not parse JSON-LD for {post_url}")
        except Exception as e:
            logging.warning(f"Error processing JSON-LD for {post_url}: {e}")

    # Fallbacks if JSON-LD is missing or incomplete
    if not metadata["author"]:
        author_meta = soup.find('meta', property='article:author_name') or \
                      soup.find('meta', attrs={'name': 'author'})
        if author_meta and author_meta.get('content'):
            metadata["author"] = author_meta['content']

    if not metadata["published_date"]:
        date_meta = soup.find('meta', property='article:published_time') or \
                    soup.find('meta', property='og:published_time') or \
                    soup.find('meta', attrs={'name': 'cXenseParse:recs:publishtime'}) # Common on some substacks
        if date_meta and date_meta.get('content'):
            metadata["published_date"] = date_meta['content']
            # Try to parse date for consistency, e.g., "2023-10-26T12:00:00.000Z" -> "YYYY-MM-DD"
            try:
                dt_obj = datetime.fromisoformat(metadata["published_date"].replace('Z', '+00:00'))
                metadata["published_date"] = dt_obj.strftime('%Y-%m-%d')
            except ValueError:
                 # Try another common format if ISO fails like "Thursday, October 26, 2023"
                try:
                    dt_obj = datetime.strptime(metadata["published_date"], '%A, %B %d, %Y')
                    metadata["published_date"] = dt_obj.strftime('%Y-%m-%d')
                except ValueError:
                    logging.warning(f"Could not parse date '{metadata['published_date']}' into YYYY-MM-DD for {post_url}. Using original.")

    # Tags might also be in meta tags (less common for substack)
    if not metadata["tags"]:
        tags_meta = soup.find_all('meta', property='article:tag')
        if tags_meta:
            metadata["tags"] = [tag.get('content') for tag in tags_meta if tag.get('content')]

    # Clean up empty tags
    metadata["tags"] = [tag for tag in metadata["tags"] if tag] if metadata["tags"] else []


    return metadata

# Parameter `request_delay` added to `get_all_post_urls`
def get_all_post_urls(substack_url, request_delay):
    """
    Fetches all post URLs from a Substack's archive pages.
    """
    all_post_urls = set()
    page = 1
    while True:
        archive_url = f"{substack_url}/archive?page={page}"
        try:
            logging.info(f"Fetching archive page: {archive_url}")
            response = requests.get(archive_url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # Refined selector logic
            links_on_page = soup.select('.portable-archive-post a[href*="/p/"], .post-preview a[href*="/p/"], a.pencraft[href*="/p/"]')
            if not links_on_page:
                links_on_page = soup.select('a[href*="/p/"]') # Broader fallback
                if not links_on_page:
                    logging.info(f"No post links found on {archive_url} using primary or fallback selectors. Assuming end of archive.")
                    break

            current_page_urls = set()
            for link_tag in links_on_page:
                href = link_tag.get('href')
                if href:
                    full_url = urljoin(substack_url, href)
                    parsed_full_url = urlparse(full_url)
                    parsed_substack_domain = urlparse(substack_url)
                    if parsed_full_url.netloc == parsed_substack_domain.netloc and "/p/" in parsed_full_url.path:
                        current_page_urls.add(full_url.split('?')[0])

            if not current_page_urls:
                logging.info(f"No valid post URLs extracted from links on {archive_url} after filtering. Assuming end of archive.")
                break

            new_urls = current_page_urls - all_post_urls
            if not new_urls:
                 logging.info(f"No new unique post URLs found on page {page} ({archive_url}). Assuming end of archive.")
                 break

            all_post_urls.update(new_urls)
            logging.info(f"Found {len(new_urls)} new post URLs on page {page}. Total unique URLs: {len(all_post_urls)}")
            page += 1
            time.sleep(request_delay)

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logging.info(f"Archive page {archive_url} returned 404. Assuming end of archive.")
            else:
                logging.error(f"HTTP error fetching archive page {archive_url}: {e}")
            break
        except requests.exceptions.RequestException as e:
            logging.error(f"Request error fetching archive page {archive_url}: {e}")
            break
        except Exception as e:
            logging.error(f"An unexpected error occurred while processing archive {archive_url}: {e}")
            break

    return list(all_post_urls)


def download_images_and_rewrite_paths(post_html_content, base_url, save_dir_for_post_assets, post_slug, assets_dir_name_param):
    """
    Downloads images from post_html_content, saves them locally, and rewrites image paths.
    - post_html_content: The HTML string of the post.
    - base_url: The base URL of the post (e.g., the post's own URL) to resolve relative image paths.
    - save_dir_for_post_assets: The directory where assets for this specific post should be saved (e.g., output/substack_slug/assets_dir_name_param/post_assets_slug).
    - post_slug: The slug of the post, used for creating unique asset subdirectories.
    - assets_dir_name_param: The name of the general assets directory (e.g., "assets" or "media_files").
    Returns the modified HTML content with updated image paths.
    """
    soup = BeautifulSoup(post_html_content, 'html.parser')
    images_found = soup.find_all('img')

    if not images_found:
        return post_html_content # No images to process

    # save_dir_for_post_assets already includes the specific post's asset path.
    # e.g. output_dir/specific_substack_slug/assets_dir_name_param/post_assets_slug
    # No, this is not correct. save_dir_for_post_assets IS ALREADY the full path to where images for THIS POST go.
    # It should be: output_dir_for_this_substack / assets_dir_name_param / post_assets_slug
    # Let's clarify the parameters and usage.
    # `save_dir_for_post_assets` should be the root for this specific post's images, e.g., current_output_dir/assets_dir_name/post_assets_slug

    os.makedirs(save_dir_for_post_assets, exist_ok=True) # This path is correct: current_output_dir/config_assets_dir_name/post_assets_slug

    for img_tag in images_found:
        original_src = img_tag.get('src')
        if not original_src:
            continue

        try:
            img_url = urljoin(base_url, original_src)

            if img_url.startswith('data:'):
                logging.info(f"Skipping data URI image: {original_src[:100]}...")
                continue

            logging.info(f"Downloading image: {img_url}")
            img_response = requests.get(img_url, stream=True, timeout=10)
            img_response.raise_for_status()

            parsed_url = urlparse(img_url)
            original_filename = os.path.basename(parsed_url.path)
            filename_base, ext_from_url = os.path.splitext(original_filename)

            content_type = img_response.headers.get('content-type')
            ext_from_content_type = mimetypes.guess_extension(content_type) if content_type else None

            image_ext = ext_from_url or ext_from_content_type or '.jpg'
            if not image_ext.startswith('.'):
                image_ext = '.' + image_ext

            safe_filename_base = slugify(filename_base) if filename_base else f"image_{images_found.index(img_tag)}"
            local_image_filename = f"{safe_filename_base}{image_ext}"
            local_image_path = os.path.join(save_dir_for_post_assets, local_image_filename) # This is correct path to save the image file

            counter = 1
            while os.path.exists(local_image_path):
                local_image_filename = f"{safe_filename_base}_{counter}{image_ext}"
                local_image_path = os.path.join(save_dir_for_post_assets, local_image_filename)
                counter += 1

            with open(local_image_path, 'wb') as f:
                for chunk in img_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Rewrite src to relative path.
            # The saved post file is in current_output_dir (e.g. output_dir/substack_slug/)
            # The image is in current_output_dir/assets_dir_name_param/post_assets_slug/local_image_filename
            # So, the relative path from the post file to the image is:
            # assets_dir_name_param/post_assets_slug/local_image_filename
            relative_image_path = os.path.join(assets_dir_name_param, post_slug, local_image_filename)

            img_tag['src'] = relative_image_path
            logging.info(f"Saved image to {local_image_path} and updated src to {relative_image_path}")

        except requests.exceptions.RequestException as e:
            logging.warning(f"Failed to download image {img_url}: {e}")
        except Exception as e:
            logging.warning(f"An error occurred processing image {original_src}: {e}")

    return str(soup)

# In process_single_post, when calling download_images_and_rewrite_paths:
# current_assets_dir_name = config_assets_dir_name (this is args.assets_dir_name)
# post_specific_assets_dir = os.path.join(output_dir, config_assets_dir_name, post_assets_slug)
# This `post_specific_assets_dir` is what gets passed as `save_dir_for_post_assets`

# So the call becomes:
# download_images_and_rewrite_paths(content_html_summary, url, post_specific_assets_dir, post_assets_slug, config_assets_dir_name)


def process_single_post(url, output_dir, formats_to_save, download_images_flag, request_delay, substack_base_url):
    """
    Downloads images from post_html_content, saves them locally, and rewrites image paths.
    - post_html_content: The HTML string of the post.
    - base_url: The base URL of the post (e.g., the post's own URL) to resolve relative image paths.
    - save_dir_for_post_assets: The directory where assets for this specific post should be saved.
    - post_slug: The slug of the post, used for creating unique asset subdirectories.
    Returns the modified HTML content with updated image paths.
    """
    soup = BeautifulSoup(post_html_content, 'html.parser')
    images_found = soup.find_all('img')

    if not images_found:
        return post_html_content # No images to process

    os.makedirs(save_dir_for_post_assets, exist_ok=True)

    for img_tag in images_found:
        original_src = img_tag.get('src')
        if not original_src:
            continue

        try:
            # Resolve relative URLs
            img_url = urljoin(base_url, original_src)

            # Skip data URIs for now, or handle them if base64 embedding is desired
            if img_url.startswith('data:'):
                print(f"Skipping data URI image: {original_src[:100]}...")
                continue

            print(f"Downloading image: {img_url}")
            img_response = requests.get(img_url, stream=True, timeout=10)
            img_response.raise_for_status()

            # Generate a filename for the image
            # Try to get extension from URL or content type
            parsed_url = urlparse(img_url)
            original_filename = os.path.basename(parsed_url.path)
            filename_base, ext_from_url = os.path.splitext(original_filename)

            content_type = img_response.headers.get('content-type')
            ext_from_content_type = mimetypes.guess_extension(content_type) if content_type else None

            image_ext = ext_from_url or ext_from_content_type or '.jpg' # Default to .jpg
            if not image_ext.startswith('.'): # ensure leading dot
                image_ext = '.' + image_ext

            # Sanitize filename_base (slugify could be too aggressive here, just basic clean)
            safe_filename_base = slugify(filename_base) if filename_base else f"image_{images_found.index(img_tag)}"

            local_image_filename = f"{safe_filename_base}{image_ext}"
            local_image_path = os.path.join(save_dir_for_post_assets, local_image_filename)

            # Ensure unique filename in case of clashes
            counter = 1
            while os.path.exists(local_image_path):
                local_image_filename = f"{safe_filename_base}_{counter}{image_ext}"
                local_image_path = os.path.join(save_dir_for_post_assets, local_image_filename)
                counter += 1

            with open(local_image_path, 'wb') as f:
                for chunk in img_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Rewrite src to relative path
            # Path relative to the main SAVE_DIR
            # e.g. if SAVE_DIR is 'output', post_assets is 'output/assets/post_slug/img.jpg'
            # and saved post is 'output/post.md', relative path is 'assets/post_slug/img.jpg'
            # For simplicity, we'll assume the asset dir is relative to where the md/html file is.
            # This might need adjustment if the md/html files are in nested structures matching substack path.
            # Current structure: SAVE_DIR/post_file.ext and SAVE_DIR/assets_dir_name/post_slug/image.ext
            assets_dir_name = "assets" # This could be configurable
            relative_image_path = os.path.join(assets_dir_name, post_slug, local_image_filename)

            img_tag['src'] = relative_image_path
            print(f"Saved image to {local_image_path} and updated src to {relative_image_path}")

        except requests.exceptions.RequestException as e:
            print(f"Failed to download image {img_url}: {e}")
        except Exception as e:
            print(f"An error occurred processing image {original_src}: {e}")

    return str(soup)

def process_single_post(url, output_dir, formats_to_save, download_images_flag, request_delay, substack_base_url):
    """
    Downloads and processes a single post.
    Saves it in the specified formats.
    """
    try:
        # Extract a slug from the URL for the filename, more reliable than title
        url_slug = url.split('/')[-1][:100]

        print(f"Downloading: {url}")
        resp = requests.get(url)
        resp.raise_for_status()
        post_html_content = resp.text

        metadata = extract_metadata_from_post(post_html_content, url)
        doc = Document(post_html_content)
        content_title = doc.title()

        if not metadata["title"]:
             metadata["title"] = content_title
        elif not content_title and metadata["title"]:
            pass
        elif content_title and metadata["title"] and len(content_title) > len(metadata["title"]):
            metadata["title"] = content_title

        if not metadata["title"]:
            metadata["title"] = "Untitled Post"
            print(f"Warning: No title found for {url}, using 'Untitled Post'.")

        content_html_summary = doc.summary()

        post_assets_slug = slugify(metadata["title"]) if metadata["title"] and metadata["title"] != "Untitled Post" else url_slug
        if not post_assets_slug:
            post_assets_slug = f"post_{url_slug}" # Fallback using url_slug

        if download_images_flag:
            # ASSETS_DIR_NAME is now sourced from config/args
            current_assets_dir_name = config_assets_dir_name # Passed to this function
            post_specific_assets_dir = os.path.join(output_dir, current_assets_dir_name, post_assets_slug)
            content_html_summary = download_images_and_rewrite_paths(content_html_summary, url, post_specific_assets_dir, post_assets_slug)

        date_prefix = metadata.get("published_date", datetime.now().strftime('%Y-%m-%d'))
        try:
            date_obj_for_filename = datetime.fromisoformat(date_prefix.split('T')[0])
            formatted_date_prefix = date_obj_for_filename.strftime('%Y%m%d')
        except ValueError:
            if len(date_prefix.split('-')) == 3 :
                 formatted_date_prefix = date_prefix.replace('-', '')
            else:
                 print(f"Warning: Could not reliably format date_prefix '{date_prefix}' for filename. Using current date.")
                 formatted_date_prefix = datetime.now().strftime('%Y%m%d')

        clean_slug = slugify(metadata["title"]) if metadata["title"] else url_slug

        for fmt in formats_to_save:
            filename = f"{formatted_date_prefix}_{clean_slug}.{fmt}"
            filepath = os.path.join(output_dir, filename)

            # Ensure the direct output directory for the file exists (e.g. if output_dir is nested)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)


            if fmt == "md":
                body_content = md(content_html_summary)
                frontmatter_str = yaml.dump(metadata, sort_keys=False, allow_unicode=True)
                final_content = f"---\n{frontmatter_str}---\n\n# {metadata['title']}\n\n{body_content}"
            elif fmt == "html":
                meta_html_comment = f"<!--\n{yaml.dump(metadata, sort_keys=False, allow_unicode=True)}-->\n"
                final_content = meta_html_comment + f"<h1>{metadata['title']}</h1>\n" + content_html_summary
            # Add other format handlers here later (json, pdf, epub)
            else:
                print(f"Warning: Unknown format '{fmt}'. Skipping.")
                continue

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(final_content)
            print(f"Successfully saved: {filepath}")

        time.sleep(request_delay)

    except requests.exceptions.RequestException as e:
        print(f"Failed to download {url}: {e}")
    except Exception as e:
        print(f"Failed to process {url}: {e}")
    return False # Indicate failure

def load_download_log(log_file_path):
    """Loads the list of processed URLs from the log file."""
    if os.path.exists(log_file_path):
        with open(log_file_path, 'r') as f:
            try:
                data = json.load(f)
                return set(data.get("processed_urls", []))
            except json.JSONDecodeError:
                logging.warning(f"Could not parse download log {log_file_path}. Starting fresh for this substack.")
                return set()
    return set()

def save_to_download_log(log_file_path, url, processed_urls_set):
    """Adds a URL to the set and saves the log file."""
    processed_urls_set.add(url)
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True) # Ensure directory exists
    with open(log_file_path, 'w') as f:
        json.dump({"processed_urls": list(processed_urls_set)}, f, indent=2)

# Renamed substack_base_url to current_substack_url for clarity in process_single_post call
# Added config_assets_dir_name to the parameters of process_single_post
# Added incremental_flag and download_log_path
def process_single_post(url, output_dir, formats_to_save, download_images_flag,
                        request_delay, current_substack_url, config_assets_dir_name,
                        incremental_flag, download_log_path, processed_urls_set):
    """
    Downloads and processes a single post.
    Saves it in the specified formats.
    Returns True if successful, False otherwise.
    """
    if incremental_flag and url in processed_urls_set:
        logging.info(f"Skipping already processed post (found in log): {url}")
        return True

    try:
        url_slug = url.split('/')[-1][:100]
        logging.info(f"Downloading: {url}")
        resp = requests.get(url, timeout=15) # Increased timeout slightly for individual posts
        resp.raise_for_status()
        post_html_content = resp.text

        metadata = extract_metadata_from_post(post_html_content, url)
        doc = Document(post_html_content)
        content_title = doc.title()

        if not metadata["title"]:
             metadata["title"] = content_title
        elif not content_title and metadata["title"]:
            pass
        elif content_title and metadata["title"] and len(content_title) > len(metadata["title"]):
            metadata["title"] = content_title

        if not metadata["title"]:
            metadata["title"] = "Untitled Post"
            logging.warning(f"No title found for {url}, using 'Untitled Post'.")

        content_html_summary = doc.summary()

        post_assets_slug = slugify(metadata["title"]) if metadata["title"] and metadata["title"] != "Untitled Post" else url_slug
        if not post_assets_slug:
            post_assets_slug = f"post_{url_slug}"

        if download_images_flag:
            post_specific_assets_dir = os.path.join(output_dir, config_assets_dir_name, post_assets_slug)
            content_html_summary = download_images_and_rewrite_paths(content_html_summary, url, post_specific_assets_dir, post_assets_slug, config_assets_dir_name)

        date_prefix = metadata.get("published_date", datetime.now().strftime('%Y-%m-%d'))
        try:
            date_obj_for_filename = datetime.fromisoformat(date_prefix.split('T')[0])
            formatted_date_prefix = date_obj_for_filename.strftime('%Y%m%d')
        except ValueError:
            if len(date_prefix.split('-')) == 3 :
                 formatted_date_prefix = date_prefix.replace('-', '')
            else:
                 logging.warning(f"Could not reliably format date_prefix '{date_prefix}' for filename {url}. Using current date.")
                 formatted_date_prefix = datetime.now().strftime('%Y%m%d')

        clean_slug = slugify(metadata["title"]) if metadata["title"] else url_slug

        for fmt in formats_to_save:
            filename = f"{formatted_date_prefix}_{clean_slug}.{fmt}"
            filepath = os.path.join(output_dir, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            if fmt == "md":
                body_content = md(content_html_summary)
                frontmatter_str = yaml.dump(metadata, sort_keys=False, allow_unicode=True)
                final_content = f"---\n{frontmatter_str}---\n\n# {metadata['title']}\n\n{body_content}"
            elif fmt == "html":
                meta_html_comment = f"<!--\n{yaml.dump(metadata, sort_keys=False, allow_unicode=True)}-->\n"
                final_content = meta_html_comment + f"<h1>{metadata['title']}</h1>\n" + content_html_summary
            elif fmt == "json":
                # For JSON, we'll store metadata and the HTML content
                json_data = {
                    "metadata": metadata,
                    "content_html": content_html_summary
                }
                final_content = json.dumps(json_data, indent=2, ensure_ascii=False)
            elif fmt in ["pdf", "epub"]:
                # For PDF and EPUB, convert HTML content using pypandoc
                # Ensure that the output directory exists for pandoc
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                try:
                    # We use content_html_summary which has image paths rewritten to be relative
                    # to the output_dir where the HTML/MD files would be.
                    # Pandoc, when converting from HTML, should be able to pick up these relative image paths
                    # if its working directory is set correctly or if paths are understandable.
                    # The `extract_media=True` option might be useful for some formats if pandoc is
                    # downloading them, but here images are already local.
                    # We'll convert the HTML (with potentially local image links) to the target format.
                    # Pandoc needs to know the base path for relative resources if they are not in the
                    # same directory as the input file (which is not the case here, as input is a string).
                    # We can create a temporary HTML file to give pandoc a clear context for relative paths.

                    temp_html_path = None
                    # Create a full HTML document for pandoc to parse metadata and content correctly.
                    full_html_for_pandoc = f"""
                    <html>
                    <head>
                        <title>{metadata.get('title', 'Untitled Post')}</title>
                        <meta name="author" content="{metadata.get('author', '')}">
                        <meta name="date" content="{metadata.get('published_date', '')}">
                    </head>
                    <body>
                        <h1>{metadata.get('title', 'Untitled Post')}</h1>
                        {content_html_summary}
                    </body>
                    </html>
                    """
                    # pypandoc.convert_text can take a string directly.
                    # For images to work, pandoc needs to be able to find them.
                    # The images are in output_dir/assets_dir_name/post_assets_slug/
                    # The output file (PDF/EPUB) is in output_dir/
                    # Pandoc's working directory is typically where the script is run.
                    # We might need to use --resource-path or ensure pandoc is run from output_dir.
                    # Or, provide absolute paths to images in the HTML, but that's more complex.

                    # Simplest approach: pypandoc converts HTML with relative image paths.
                    # If these paths are relative to `output_dir`, it might work if pandoc
                    # implicitly understands this or if we can hint at it.
                    # Let's try converting directly first.
                    # `extra_args` can be used to pass pandoc command line options.
                    # The `outputfile` argument in `convert_text` handles saving.

                    # Pandoc's default behavior is that relative paths are resolved relative to the *current working directory*.
                    # Our image paths in content_html_summary are like: "assets_dir_name/post_assets_slug/image.jpg"
                    # If the script is run from the repo root, pandoc needs to be told where these assets are.
                    # One way is to change CWD for the pandoc call, or use `--resource-path`.
                    # Let's try with resource_path. The resource path should be `output_dir`.
                    # However, pypandoc doesn't directly expose --resource-path in convert_text in a simple way for multiple paths.
                    # A more robust way: create a temporary HTML file *inside* output_dir,
                    # so all relative paths are correct from its perspective.

                    temp_html_filename = f"{formatted_date_prefix}_{clean_slug}_temp.html"
                    temp_html_path = os.path.join(output_dir, temp_html_filename)

                    with open(temp_html_path, "w", encoding="utf-8") as temp_f:
                        temp_f.write(full_html_for_pandoc)

                    # Now convert this temporary HTML file. Pandoc will resolve relative paths
                    # (like "assets_dir_name/post_slug/image.png") from the location of this temp HTML file.
                    pypandoc.convert_file(
                        temp_html_path,
                        fmt,  # to format (pdf or epub)
                        outputfile=filepath,
                        extra_args=['--embed-resources', '--standalone'] # Embed images and create a standalone file
                    )
                    logging.info(f"Successfully converted and saved: {filepath} using pandoc")

                    if temp_html_path and os.path.exists(temp_html_path):
                        os.remove(temp_html_path)

                except Exception as e: # Catch pypandoc.PandocError or any other
                    logging.error(f"Failed to convert to {fmt} for {url} using pandoc: {e}")
                    # If pandoc is not installed, pypandoc.convert_text will raise an OSError/FileNotFoundError
                    if "No pandoc was found" in str(e) or "pandoc executable not found" in str(e):
                        logging.error("Pandoc is not installed or not found in PATH. Please install pandoc to use PDF/EPUB formats.")
                        # We could try to skip further pandoc conversions for this run
                    if temp_html_path and os.path.exists(temp_html_path): # Clean up temp file on error too
                        os.remove(temp_html_path)
                    continue # Skip to next format or post
            else:
                logging.warning(f"Unknown format '{fmt}'. Skipping file: {filename}")
                continue

            # Common save logic for MD, HTML, JSON (PDF/EPUB are saved by pandoc directly)
            if fmt not in ["pdf", "epub"]:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(final_content)
                logging.info(f"Successfully saved: {filepath}")

        time.sleep(request_delay)

        if incremental_flag:
            save_to_download_log(download_log_path, url, processed_urls_set)
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {url}: {e}")
    except Exception as e:
        logging.error(f"Failed to process {url}: {e}")
    return False


def cli():
    # First, try to load arguments for config path *before* full parsing
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("-c", "--config", default=CONFIG_FILE_NAME,
                            help="Path to a YAML configuration file.")
    config_args, _ = pre_parser.parse_known_args()
    config = load_config(config_args.config)
    args = init_argparse(config)

    # Setup logging
    log_level = logging.INFO
    # Consider adding --verbose flag later to set logging.DEBUG
    logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    logging.info("Substack Downloader started.")
    logging.info(f"Using configuration from: {config_args.config if os.path.exists(config_args.config) else 'Defaults / CLI args only'}")


    formats_to_save = [f.strip().lower() for f in args.formats.split(',')]

    for substack_idx, current_substack_url in enumerate(args.substack_urls):
        if not current_substack_url:
            logging.warning("Empty Substack URL found in list. Skipping.")
            continue

        if substack_idx > 0: # Add a separator if processing more than one substack
            logging.info("-" * 70)

        logging.info(f"Processing Substack ({substack_idx+1}/{len(args.substack_urls)}): {current_substack_url}")

        current_output_dir = args.output_dir
        if len(args.substack_urls) > 1:
            try:
                substack_domain = urlparse(current_substack_url).netloc
                substack_slug = slugify(substack_domain) if substack_domain else f"substack_{substack_idx+1}"
                current_output_dir = os.path.join(args.output_dir, substack_slug)
            except Exception as e:
                logging.warning(f"Could not generate slug for Substack URL {current_substack_url}. Using generic name. Error: {e}")
                current_output_dir = os.path.join(args.output_dir, f"substack_{substack_idx+1}")

        os.makedirs(current_output_dir, exist_ok=True)

        _current_substack_url_normalized = current_substack_url.rstrip('/') + '/'

        logging.info(f"Saving posts to: {current_output_dir}")
        logging.info(f"Formats: {', '.join(formats_to_save)}")
        logging.info(f"Download images: {'Yes' if args.download_images else 'No'}")
        logging.info(f"Request delay: {args.delay}s")

        processed_urls_for_this_substack = set()
        download_log_file = None
        if args.incremental:
            logging.info("Incremental download: Enabled")
            download_log_file = os.path.join(current_output_dir, ".download_log.json")
            processed_urls_for_this_substack = load_download_log(download_log_file)
            logging.info(f"Found {len(processed_urls_for_this_substack)} previously processed URLs in {download_log_file}")

        all_post_urls = get_all_post_urls(_current_substack_url_normalized, args.delay)
        logging.info(f"Found {len(all_post_urls)} total posts for this Substack...")

        if not all_post_urls:
            logging.info("No posts found for this Substack. Continuing to next if any.")
            continue

        successful_downloads = 0
        skipped_downloads = 0
        failed_downloads = 0

        for i, url in enumerate(all_post_urls):
            logging.info(f"  Processing post {i+1}/{len(all_post_urls)}: {url}")

            # Check for skipping directly here before calling process_single_post for clarity
            if args.incremental and url in processed_urls_for_this_substack:
                logging.info(f"  Skipping already processed post (found in log): {url}")
                skipped_downloads +=1
                continue

            success = process_single_post(
                url=url,
                output_dir=current_output_dir,
                formats_to_save=formats_to_save,
                download_images_flag=args.download_images,
                request_delay=args.delay,
                current_substack_url=_current_substack_url_normalized,
                config_assets_dir_name=args.assets_dir_name,
                incremental_flag=args.incremental, # Pass it down so it can log to file on success
                download_log_path=download_log_file,
                processed_urls_set=processed_urls_for_this_substack # Pass the live set to be updated
            )
            if success:
                successful_downloads +=1
            else:
                failed_downloads +=1

        logging.info(f"Substack {current_substack_url} processing complete.")
        logging.info(f"Summary: {successful_downloads} downloaded, {skipped_downloads} skipped, {failed_downloads} failed.")

    logging.info("All specified Substacks processed. Done.")

if __name__ == "__main__":
    cli()
