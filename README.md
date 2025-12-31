# 🚀 VestX - Stock Compensation Tracker

A comprehensive web application for tracking VestX stock compensation packages, including RSUs, ISOs, ESPP, and various grant types with automatic vesting schedule calculations and tax management.

## ✨ Features

### Grant Management
- Track New Hire, Annual Performance, Promotion, Kickass, ESPP, and nqESPP grants
- Support for RSU, ISO (5-year/6-year), and Cash compensation
- Automatic vesting schedule calculation with smart cliff periods
- Semi-annual and monthly vesting support
- Edit grants and auto-recalculate vest schedules

### Dashboard & Analytics
- Real-time portfolio valuation with ISO spread calculation
- Interactive vesting timeline with historical price points
- Stock price history charts and mini-charts
- Upcoming vest notifications
- Complete vesting schedule view with sortable columns

### Tax Management
- Track cash-to-cover vs shares-sold-to-cover for each vest
- Record "Cash Paid", "Shares Sold", and auto-calculate "Shares Received"
- Calculate vest values at different stock prices
- Historical tracking of net shares received

### Security & Access
- User authentication with password encryption
- Admin panel for stock price management
- User data isolation and privacy
- Admin user management

### Modern UI
- Dark theme with vibrant accent colors (Robinhood-inspired)
- Responsive design for all devices
- Chart.js visualizations with zoom controls
- Sortable tables with click-to-sort columns

## 🚀 Quick Start

### Prerequisites
- Python 3.8+

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/stonks.git
   cd stonks
   ```

2. **Create virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env and set your SECRET_KEY
   ```

5. **Run the application:**
   ```bash
   python main.py
   ```

6. **Access the app:**
   - Navigate to `http://127.0.0.1:5001`
   - Default admin login: `admin` / `admin` (change immediately!)

## 📖 User Guide

## 📖 User Guide

### Adding Your First Grant
1. Register/login to your account
2. Click "Add Grant" on the dashboard
3. Enter grant details (date, type, shares)
4. View automatically calculated vesting schedule

### Managing Vests
- Click "My Grants" to see all grants
- Click any grant to view details and vesting schedule
- For each vest event, record tax withholding:
  - Enter "Cash Paid" if you paid cash for taxes
  - Select "Fully Covered?" if all taxes were covered
  - Enter "Shares Sold" if shares were sold for taxes
  - "Shares Received" auto-calculates

### Admins Only
- Manage stock prices via Admin → Stock Prices
- Add new valuations as VestX shares are revalued
- View all users via Admin → Users

## 🏗️ Grant Types Reference

| Grant Type | Vest Period | Cliff | Frequency |
|------------|-------------|-------|-----------|
| New Hire (RSU) | 5 years | 1 year | Semi-annual (6/15, 11/15) |
| Annual Bonus - Short (RSU/Cash) | 1 year | 1 year | One-time |
| Annual Bonus - Long (RSU) | 4 years | 1.5 years | Semi-annual |
| Annual Bonus - Long (ISO 5Y) | 4 years | 1.5 years | Monthly |
| Annual Bonus - Long (ISO 6Y) | 4 years | 2.5 years | Monthly |
| Promotion (RSU) | 5 years | 1 year | Semi-annual |
| Kickass (RSU) | 1-5 years | 1 year | Semi-annual |
| ESPP | Immediate | None | Specific dates |

**Note:** ISOs use spread value (current price - strike price) for valuation.

## 🏛️ Project Structure

```
stonks/
├── app/
│   ├── models/              # Database models (User, Grant, VestEvent, StockPrice)
│   ├── routes/              # Flask blueprints (auth, main, grants, admin)
│   ├── templates/           # Jinja2 HTML templates
│   ├── static/              # CSS, JavaScript, assets
│   │   ├── css/style.css
│   │   └── js/sortable-tables.js
│   └── utils/               # Helpers (vest calculator, DB init)
├── instance/                # SQLite database (gitignored)
├── main.py                  # Application entry point
├── requirements.txt         # Python dependencies
├── Procfile                 # Heroku deployment
├── Dockerfile               # Container deployment
└── README.md
```

## 🛠️ Technology Stack

- **Backend:** Flask 3.0, SQLAlchemy, Flask-Login
- **Database:** SQLite (dev), PostgreSQL-ready
- **Frontend:** HTML5, CSS3, Vanilla JavaScript
- **Charts:** Chart.js 4.4 with date adapters
- **Security:** Werkzeug password hashing

## 🚀 Deployment

### Heroku
```bash
heroku create your-app-name
heroku config:set SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
heroku addons:create heroku-postgresql:mini
git push heroku main
```

### Docker
```bash
docker build -t vestx .
docker run -p 5001:5001 \
  -e SECRET_KEY=your-secret-key \
  -e DATABASE_URL=postgresql://... \
  vestx
```

### GitHub Setup
1. Create a new private repository on GitHub
2. Add remote and push:
   ```bash
   git remote add origin git@github.com:YOUR_USERNAME/stonks.git
   git branch -M main
   git push -u origin main
   ```
3. Configure repository settings:
   - Enable Dependabot security alerts
   - Keep repository private (contains financial tracking)
   - Never commit the `instance/` directory

## 🔒 Security Notes

⚠️ **Production Checklist:**
- [ ] Change default admin password immediately
- [ ] Generate strong SECRET_KEY: `python -c 'import secrets; print(secrets.token_hex(32))'`
- [ ] Enable HTTPS/SSL
- [ ] Use PostgreSQL instead of SQLite
- [ ] Set up proper email for password resets
- [ ] Never commit `.env` or `instance/*.db` files
- [ ] Enable Dependabot and security scanning on GitHub

## 📝 Development

**Code quality:**
```bash
# Format code
black app/

# Lint
flake8 app/

# Type check
mypy app/
```

**Database migrations:**
```bash
# If adding new fields, use app/utils/migrate_db.py
# Or for new setups, delete instance/stonks.db and restart
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

Private - All Rights Reserved

## 💡 Acknowledgments

Built for VestX users to better track and understand their stock compensation packages.

---

**Status:** Production Ready ✅  
**Last Updated:** December 2025
