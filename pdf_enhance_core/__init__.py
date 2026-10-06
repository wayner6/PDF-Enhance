__version__ = "1.1.0"

from .detector import check_pdf_text_layer, text_layer_stats, text_quality_stats, scan_layer_stats, detect_toc_pages, detect_toc_pages_with_ocr, parse_page_range
from .parser import parse_toc_from_pages, parse_toc_from_ocr_pages, TOCItem
from .offset_finder import detect_page_offset, detect_page_offset_with_ocr
from .toc_io import export_to_toc_file, import_from_toc_file
from .bookmark_writer import apply_bookmarks
from .ocr_engine import get_ocr_engine, ocr_pdf_page
from .pdf_ocr_pipeline import generate_searchable_pdf
from .ai_toc_parser import parse_toc_with_vision_ai
from .config import load_ai_config, load_general_config

__all__ = [
    "check_pdf_text_layer",
    "text_layer_stats",
    "text_quality_stats",
    "scan_layer_stats",
    "detect_toc_pages",
    "detect_toc_pages_with_ocr",
    "parse_page_range",
    "parse_toc_from_pages",
    "parse_toc_from_ocr_pages",
    "TOCItem",
    "detect_page_offset",
    "detect_page_offset_with_ocr",
    "export_to_toc_file",
    "import_from_toc_file",
    "apply_bookmarks",
    "get_ocr_engine",
    "ocr_pdf_page",
    "generate_searchable_pdf",
    "parse_toc_with_vision_ai",
    "load_ai_config",
    "load_general_config"
]
