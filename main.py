"""
Main entry point for the VestX Stock Compensation application.
Updated: 2026-01-18
"""

from app import create_app
import os

app = create_app()

if __name__ == "__main__":
    # Get port from environment or use default
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    
    print("\n" + "="*60)
    print("🚀 VestX - Stock Compensation Tracker")
    print("="*60)
    print(f"\nServer starting at: http://127.0.0.1:{port}")
    print(f"Admin login: username='admin', password='admin'")
    print("\nPress CTRL+C to quit\n")
    
    app.run(host='0.0.0.0', port=port, debug=debug)
