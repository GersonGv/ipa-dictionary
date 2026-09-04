"""Passenger entry point.

cPanel's Python App imports this file and looks for a module-level callable
named `application`. Nothing else in the project should import it.
"""

from app import create_app

application = create_app()
