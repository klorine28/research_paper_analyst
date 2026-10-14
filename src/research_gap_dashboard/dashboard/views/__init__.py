"""
One module per dashboard page, each exposing `render_page(chosen)`.

The shell (`dashboard.app`) dispatches to these through `dashboard.page_registry`.
Like the rest of the dashboard package, nothing here imports a pipeline stage.
"""
