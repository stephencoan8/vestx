"""
Main entry point for the VestX Stock Compensation application.
"""

from app import create_app
import os
import logging

# Production: INFO. Local/dev: DEBUG when FLASK_ENV=development
_log_level = logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO
logging.basicConfig(
    level=_log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Never dump full Grok request bodies (account lots, prompts) to Railway logs
for _noisy in (
    'openai', 'openai._base_client',
    'httpx', 'httpx2', 'httpcore', 'httpcore2', 'httpcore.http11',
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'

    print("\n" + "=" * 60)
    print("VestX - Stock Compensation Tracker")
    print("=" * 60)
    print(f"\nServer starting at: http://127.0.0.1:{port}")
    print("\nPress CTRL+C to quit\n")

    app.run(host='0.0.0.0', port=port, debug=debug)
