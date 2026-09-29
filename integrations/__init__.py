"""External-data adapters for geocoding, place research, and routing.

Each public function returns the tool-result envelope proposed in docs/CONTRACTS.md:
{"ok", "data", "error", "warnings"}. These are adapters, not registered tools; tools.py
remains the central registry.
"""
