"""This file contains Custom Metadata for your API Project.

Be aware, this will be re-generated any time you run the
'api-admin custom metadata' command!
"""

from app.config.helpers import MetadataBase

custom_metadata = MetadataBase(
    title="HeadsUp API",
    name="headsup-api",
    description=(
        "The backend for HeadsUp, an AI-powered personal monitoring assistant. "
        "Stay informed without having to keep checking."
    ),
    repository="",
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    contact={
        "name": "",
        "url": "",
    },
    email="",
    year="2026",
)
