from core.api.routes.wiki_routes import register_wiki_routes


def register_routes(app):
    register_wiki_routes(app)
    # other existing route registrations...
