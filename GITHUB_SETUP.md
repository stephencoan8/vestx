# How to Push SpaceX Stonks to GitHub

## Quick Setup (5 minutes)

### 1. Create GitHub Repository
1. Go to https://github.com/new
2. Repository name: `stonks`
3. Description: `SpaceX Stock Compensation Tracker - Track RSUs, ISOs, ESPP with vesting schedules and tax calculations`
4. **Make it Private** (contains sensitive financial tracking code)
5. **DO NOT** initialize with README, .gitignore, or license (we already have these)
6. Click "Create repository"

### 2. Push to GitHub
After creating the repo, GitHub will show you commands. Use these:

```bash
cd /Users/stephencoan/stonks

# Add your GitHub repo as remote
git remote add origin https://github.com/YOUR_USERNAME/stonks.git

# Push to GitHub
git branch -M main
git push -u origin main
```

**Replace `YOUR_USERNAME` with your actual GitHub username!**

### 3. Alternative: Using SSH (Recommended for security)
If you have SSH keys set up with GitHub:

```bash
cd /Users/stephencoan/stonks
git remote add origin git@github.com:YOUR_USERNAME/stonks.git
git branch -M main
git push -u origin main
```

## What's Included

Your repository now contains:
- ✅ Complete Flask application
- ✅ All templates and static files
- ✅ Database schema and models
- ✅ Comprehensive documentation (7 markdown files)
- ✅ Requirements.txt with all dependencies
- ✅ .gitignore (protects sensitive data)
- ✅ Docker support
- ✅ Production-ready setup

## Security Notes

### Files Excluded (.gitignore)
The following are NOT pushed to GitHub (protected):
- `.venv/` - Your virtual environment
- `instance/stonks.db` - **WAIT! This is included!** See below
- `.env` - Environment variables
- `__pycache__/` - Python cache
- Test files

### ⚠️ IMPORTANT: Database Security
The database `instance/stonks.db` is currently tracked by git. If it contains real financial data:

```bash
# Remove database from git tracking
git rm --cached instance/stonks.db

# Add to .gitignore
echo "instance/*.db" >> .gitignore

# Commit the change
git add .gitignore
git commit -m "Remove database from version control"
git push
```

## Repository Settings Recommendations

After pushing to GitHub, configure these settings:

### 1. Branch Protection (Settings → Branches)
- Protect `main` branch
- Require pull request reviews
- Require status checks to pass

### 2. Security (Settings → Code security and analysis)
- Enable Dependabot alerts
- Enable Dependabot security updates
- Enable secret scanning

### 3. Visibility
- Keep repository **Private** unless you want to open-source it
- If making public, ensure no sensitive data in commit history

## Repository Description Suggestions

**Short description:**
> Track SpaceX stock compensation (RSUs, ISOs, ESPP) with automatic vesting schedules, tax calculations, and beautiful visualizations

**Topics (tags):**
- `spacex`
- `stock-compensation`
- `rsu`
- `iso`
- `espp`
- `vesting-schedule`
- `flask`
- `python`
- `finance`
- `equity-tracking`

## Future Git Workflow

### Making Changes
```bash
# Make your changes...
git add .
git commit -m "Description of changes"
git push
```

### Creating Feature Branches
```bash
# Create a new feature branch
git checkout -b feature/new-report

# Make changes and commit
git add .
git commit -m "Add new report feature"

# Push branch
git push -u origin feature/new-report

# Create pull request on GitHub
# Merge when ready
```

### Updating from GitHub
```bash
git pull origin main
```

## Clone on Another Machine
```bash
git clone https://github.com/YOUR_USERNAME/stonks.git
cd stonks
python3 -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install -r requirements.txt
python main.py
```

## Backup Strategy

Your code is now backed up on GitHub! For complete safety:
1. **Code**: GitHub (done! ✅)
2. **Database**: Backup `instance/stonks.db` separately
3. **Environment**: Document any custom `.env` settings

## README Badge Ideas

Add these to your README.md for style points:

```markdown
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/flask-3.0+-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
```

## Share Your Work

If you want to showcase this project:
1. Make repository public
2. Add screenshots to README
3. Create a demo video
4. Share on LinkedIn/Twitter
5. Add to your portfolio

---

**Status:** ✅ Ready to push to GitHub
**Commit:** c260e52
**Files:** 49 files, 5,583 insertions
**Branch:** master → main (will be renamed on push)

**Next Step:** Create the GitHub repo and run the push commands above! 🚀
