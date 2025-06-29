# substack-dl

A tool to download Substack posts and archives.

## Features

- Download posts from one or more Substack URLs.
- Save posts in multiple formats: Markdown (.md), HTML (.html), JSON (.json), PDF (.pdf), and EPUB (.epub).
- Download images and rewrite links to local paths.
- Incremental downloads: skip posts that have already been downloaded.
- Customizable output directory and asset directory names.
- Configuration via YAML file or command-line arguments.

## Installation

### Prerequisites

- Python 3.8+
- [Pandoc](https://pandoc.org/installing.html): Required for PDF and EPUB output formats. Please install it and ensure it's in your system's PATH.

### Using pip (from PyPI - if published)

```bash
pip install substack-dl
```

*(Note: Publishing to PyPI is a future step. For now, use the local installation method below.)*

### Local Installation (from source)

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/substack-dl.git # Replace with the actual URL
    cd substack-dl
    ```

2.  **Install Poetry (if you don't have it):**
    ```bash
    pip install poetry
    ```
    Or see [official Poetry installation instructions](https://python-poetry.org/docs/#installation).

3.  **Install dependencies using Poetry:**
    ```bash
    poetry install
    ```
    This will create a virtual environment and install all necessary packages.

Alternatively, you can use the provided Makefile for common development tasks:

*   **Set up the environment and install dependencies:**
    ```bash
    make setup
    ```
*   **Run the application:**
    ```bash
    make run
    ```
    This is equivalent to `poetry run substack-dl` followed by your desired arguments. For example:
    ```bash
    make run -- --url https://yourfavoritesubstack.substack.com/
    # or to see help
    make run -- --help
    ```
*   **Run tests:**
    ```bash
    make test
    ```
*   **Clean up the environment:**
    ```bash
    make clean
    ```

## Usage

Once installed (either manually with Poetry or using `make setup`), you can run the tool using the `substack-dl` command (or `make run`).

### Command-Line Interface

```bash
substack-dl [OPTIONS]
```

**Basic example:**

```bash
substack-dl --url https://yourfavoritesubstack.substack.com/
```

This will download all posts from the specified Substack and save them in the default formats (Markdown and JSON) to the `substack_posts/` directory.

**Options:**

*   `-u, --url SUBSTACK_URL`: URL of a single Substack to download. Overrides URLs in the config file.
*   `-c, --config PATH`: Path to a YAML configuration file (default: `config.yaml`).
*   `-f, --formats FMT1,FMT2`: Comma-separated list of output formats (e.g., `md,html,json,pdf,epub`).
    *   Available: `md`, `html`, `json`, `pdf`, `epub`.
    *   Default: `md,json`.
*   `-o, --output-dir DIRECTORY`: Directory where posts will be saved (default: `substack_posts`).
*   `-i, --download-images / --no-download-images`: Download images and rewrite links (default: enabled).
*   `--incremental / --no-incremental`: Enable incremental downloads (default: disabled).
*   `-d, --delay SECONDS`: Delay in seconds between network requests (default: `1.0`).
*   `--assets-dir-name NAME`: Name of the subdirectory for storing image assets (default: `assets`).

**Example with multiple formats and output directory:**

```bash
substack-dl --url https://example.substack.com/ --formats md,pdf,epub --output-dir my_substack_archive
```

### Configuration File

You can use a `config.yaml` file to specify your settings. CLI arguments will override settings from the config file.

**Default `config.yaml` structure:**

```yaml
substack_urls:
  - https://some.substack.com/
  - https://another.substack.com/
formats: ["md", "json"] # Default formats
output_dir: "substack_posts"
download_images: true
incremental: false
delay: 1.0
assets_dir_name: "assets"
```

Place `config.yaml` in the directory where you run `substack-dl`, or specify its path using the `-c` option.

## Development

1.  Follow the local installation steps above.
2.  Activate the virtual environment created by Poetry:
    ```bash
    poetry shell
    ```
3.  Run tests (see Testing section below).

## Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.