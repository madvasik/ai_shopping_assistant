# Services module
from .intent_classifier import classify_intent, is_catalog_related, find_product_in_catalog, extract_product_names_from_query
from .product_search import load_products, Retriever
from .knowledge_base import CatalogKB, KBAnswer
from .task_analyzer import get_required_products_for_task, should_ask_clarification

__all__ = [
    'classify_intent',
    'is_catalog_related', 
    'find_product_in_catalog',
    'extract_product_names_from_query',
    'load_products',
    'Retriever',
    'CatalogKB',
    'KBAnswer',
    'get_required_products_for_task',
    'should_ask_clarification',
]
