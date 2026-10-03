"""Signatures de CMS / e-commerce reconnues dans le HTML d'un site (indices objectifs, jamais l'apparence)."""
CMS_SIGNATURES = [
    ("WordPress", r"wp-content|wp-includes|/wp-json/|content=\"wordpress"),
    ("Shopify", r"cdn\.shopify\.com|shopify\.theme|myshopify\.com"),
    ("PrestaShop", r"prestashop|/modules/ps_|var\s+prestashop"),
    ("Wix", r"wixstatic\.com|wix\.com|_wixcidx"),
    ("Jimdo", r"jimdo(?:site|free)?\.com|jimcdn\.com"),
    ("Webself", r"webself\.net"),
    ("e-monsite", r"e-monsite\.com"),
    ("SiteW", r"sitew\.(?:fr|com)"),
    ("Squarespace", r"squarespace\.com|static1\.squarespace"),
    ("Webflow", r"webflow\.com|data-wf-page"),
    ("Joomla", r"/media/jui/|joomla"),
    ("Drupal", r"drupal-settings-json|/sites/default/files|content=\"drupal"),
    ("Magento", r"/static/frontend/|mage/cookies|magento"),
]
ECOM_SIGNATURES = r"woocommerce|add[-_]to[-_]cart|ajouter au panier|panier|shopify|prestashop|magento|/checkout"
SITE_BUILDERS = {"Wix", "Jimdo", "Webself", "e-monsite", "SiteW"}      # créateurs de sites « faits soi-même » : argument de refonte, jamais un défaut à lui seul
