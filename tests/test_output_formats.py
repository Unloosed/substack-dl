import os
import shutil
import pytest
from unittest.mock import patch, MagicMock
import sys
import requests # For requests.exceptions.HTTPError

# Add the parent directory to sys.path to allow importing substack_dl
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from substack_dl import main

# Mock config that will be used by tests
MOCK_CONFIG_DEFAULT = {
    "substack_urls": ["http://mock.substack.com"],
    "formats": ["md", "json", "html"], # Default test formats
    "output_dir": "test_output",
    "download_images": False, # Disable images for simpler testing
    "incremental": False,
    "delay": 0.01,
    "assets_dir_name": "test_assets"
}

# Mock post data
MOCK_POST_URL = "http://mock.substack.com/p/mock-post"
MOCK_POST_HTML_CONTENT = """
<html>
    <head>
        <title>Mock Post Title</title>
        <meta property="article:published_time" content="2023-01-01T12:00:00.000Z" />
        <meta property="article:author_name" content="Mock Author" />
    </head>
    <body>
        <h1>Mock Post Title</h1>
        <p>This is mock post content.</p>
        <img src="http://mock.substack.com/image.jpg" alt="mock image" />
    </body>
</html>
"""
MOCK_METADATA = {
    "title": "Mock Post Title",
    "author": "Mock Author",
    "published_date": "2023-01-01",
    "tags": [],
    "url": MOCK_POST_URL
}

@pytest.fixture(scope="function")
def setup_test_environment(tmp_path):
    """Create a temporary output directory for tests and clean up afterwards."""
    test_output_dir = tmp_path / MOCK_CONFIG_DEFAULT["output_dir"]
    os.makedirs(test_output_dir, exist_ok=True)

    # Mock args for main.cli()
    mock_args = MagicMock()
    mock_args.url = MOCK_CONFIG_DEFAULT["substack_urls"][0] # Single URL for testing
    mock_args.substack_urls = [MOCK_CONFIG_DEFAULT["substack_urls"][0]]
    mock_args.config = "dummy_config.yaml" # Not actually loaded due to CLI URL override
    mock_args.formats = ",".join(MOCK_CONFIG_DEFAULT["formats"])
    mock_args.output_dir = str(test_output_dir) # Use tmp_path for output
    mock_args.download_images = MOCK_CONFIG_DEFAULT["download_images"]
    mock_args.incremental = MOCK_CONFIG_DEFAULT["incremental"]
    mock_args.delay = MOCK_CONFIG_DEFAULT["delay"]
    mock_args.assets_dir_name = MOCK_CONFIG_DEFAULT["assets_dir_name"]

    yield test_output_dir, mock_args

    # Teardown: shutil.rmtree can be used if tmp_path doesn't clean up everything as expected,
    # but pytest's tmp_path fixture usually handles this well.
    # For explicit cleanup if needed:
    # if os.path.exists(test_output_dir):
    #     shutil.rmtree(test_output_dir)

def mock_requests_get(*args, **kwargs):
    """Mock requests.get to return predefined responses."""
    url = args[0]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    if "/archive" in url:
        # Simulate finding one post on the first archive page, then no more posts
        if "page=1" in url:
            mock_response.status_code = 200
            mock_response.content = f'<a href="{MOCK_POST_URL}">Mock Post</a>'.encode('utf-8')
        else: # page > 1
            mock_response.status_code = 200 # Or 404, but empty content also signals end
            mock_response.content = b'' # No links, signals end of archive
    elif url == MOCK_POST_URL:
        mock_response.status_code = 200
        mock_response.text = MOCK_POST_HTML_CONTENT
    else: # Default for other URLs (e.g., images, which are disabled in test config)
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
    return mock_response


@patch('substack_dl.main.requests.get', side_effect=mock_requests_get)
@patch('substack_dl.main.pypandoc.convert_file') # Mock pandoc conversion
def test_output_formats_created(mock_pandoc_convert, mock_get, setup_test_environment):
    """Test if files with specified formats (md, json, html, pdf, epub) are created."""
    test_output_dir, mock_cli_args = setup_test_environment

    # Add pdf and epub to the formats for this specific test
    test_formats = ["md", "json", "html", "pdf", "epub"]
    mock_cli_args.formats = ",".join(test_formats)

    # Mock argparse to return our mock_cli_args
    with patch('argparse.ArgumentParser.parse_args', return_value=mock_cli_args):
        # Mock load_config to return a basic config, as parse_args is fully mocked
        with patch('substack_dl.main.load_config', return_value=MOCK_CONFIG_DEFAULT):
            main.cli() # Run the main CLI function

    # Check for created files
    expected_slug = "20230101_mock-post-title" # Based on MOCK_METADATA

    # Adjust expected output directory based on the number of URLs
    # main.py logic: if len(urls) > 1, creates a subfolder.
    if len(mock_cli_args.substack_urls) > 1:
        output_subdir = test_output_dir / "mock-substack-com"
    else:
        output_subdir = test_output_dir # Files go directly into the main test_output_dir

    os.makedirs(output_subdir, exist_ok=True) # Ensure it exists for the test checks

    for fmt in test_formats:
        expected_file_path = output_subdir / f"{expected_slug}.{fmt}"

        if fmt not in ["pdf", "epub"]:
            assert os.path.exists(expected_file_path), f"File {expected_file_path} was not created for format {fmt}."
            with open(expected_file_path, "r", encoding="utf-8") as f:
                content = f.read()
                assert "Mock Post Title" in content, f"Content missing in {expected_file_path}"
        else: # For PDF/EPUB, we check that pypandoc.convert_file was called correctly
            # The file itself won't exist because pypandoc.convert_file is mocked.
            # Check if pandoc.convert_file was called for pdf and epub
            # The call for pdf:
            # pypandoc.convert_file(source_file_path, 'pdf', outputfile=expected_pdf_path, ...)
            # The call for epub:
            # pypandoc.convert_file(source_file_path, 'epub', outputfile=expected_epub_path, ...)
            found_call = False
            for call_args in mock_pandoc_convert.call_args_list:
                # call_args is a tuple: (args, kwargs)
                # We are interested in kwargs['outputfile'] and args[1] (format)
                if call_args.kwargs.get('outputfile') == str(expected_file_path) and call_args.args[1] == fmt:
                    found_call = True
                    temp_html_input_path_str = call_args.args[0]
                    # Check that the path for the temporary HTML input file looks correct.
                    # We don't check os.path.exists() because the file is deleted by the main code after use.
                    assert temp_html_input_path_str.startswith(str(output_subdir)), \
                        f"Pandoc temp input file path '{temp_html_input_path_str}' should start with '{str(output_subdir)}' for {fmt}"
                    assert temp_html_input_path_str.endswith("_temp.html"), \
                        f"Pandoc temp input file path '{temp_html_input_path_str}' should end with '_temp.html' for {fmt}"
                    # Check that the expected slug is part of the temp file name
                    assert expected_slug in temp_html_input_path_str, \
                        f"Expected slug '{expected_slug}' not in Pandoc temp input file path '{temp_html_input_path_str}' for {fmt}"
                    break
            assert found_call, f"pypandoc.convert_file was not called correctly for {fmt}"

    # Ensure requests.get was called for archive and post
    assert any("/archive" in call.args[0] for call in mock_get.call_args_list), "requests.get not called for archive"
    assert any(MOCK_POST_URL in call.args[0] for call in mock_get.call_args_list), "requests.get not called for post URL"

# To run this test:
# 1. Ensure pytest and unittest.mock are installed (poetry should handle this via dev dependencies)
# 2. Navigate to the root of the project in your terminal
# 3. Run: poetry run pytest
#
# Note: This test mocks network calls and pandoc itself.
# It verifies that the main CLI flow results in attempting to create files in the correct formats
# and that pandoc is invoked for PDF/EPUB.
# It does NOT verify the *content* of PDF/EPUB files, only that the conversion process was initiated.
# It also assumes that if `download_images` is False, the image downloading logic is skipped,
# simplifying the test's focus on output file creation.
# The `requests.exceptions.HTTPError` had to be imported for the mock.

# A small helper to ensure cleanup of temp files potentially left by pandoc if tests fail early
@pytest.fixture(autouse=True)
def cleanup_temp_files(setup_test_environment):
    test_output_dir, mock_cli_args = setup_test_environment # Need mock_cli_args to check len(substack_urls)

    # Determine the correct subdir for cleanup, same logic as in the test
    if len(mock_cli_args.substack_urls) > 1:
        actual_output_path_for_files = test_output_dir / "mock-substack-com"
    else:
        actual_output_path_for_files = test_output_dir

    yield # Test runs here

    # After test, clean up any _temp.html files from the actual output path
    if os.path.exists(actual_output_path_for_files):
        for item in os.listdir(actual_output_path_for_files):
            if item.endswith("_temp.html"):
                try:
                    os.remove(os.path.join(actual_output_path_for_files, item))
                except OSError:
                    pass # Ignore if already removed or other issue
