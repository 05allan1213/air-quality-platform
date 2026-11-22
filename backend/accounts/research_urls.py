from django.urls import path

from .research_exports import download_export, export_ticket, exports
from .research_saved import run_saved_query, saved_queries, saved_query

urlpatterns = [
    path("me/saved-queries/", saved_queries),
    path("me/saved-queries/<uuid:item_id>/", saved_query),
    path("me/saved-queries/<uuid:item_id>/run/", run_saved_query),
    path("me/exports/", exports),
    path("me/exports/<uuid:item_id>/", export_ticket),
    path("me/exports/<uuid:item_id>/download/", download_export),
]
