# Push to GitHub - Quick Guide

## Prerequisites
✅ All code is committed locally  
✅ .gitignore is configured  
✅ Database is excluded from git  
✅ Documentation is complete

## Method 1: Using the Helper Script (Easiest)

1. **Create the repository on GitHub:**
   - Go to https://github.com/new
   - Repository name: `stonks`
   - Description: `SpaceX Stock Compensation Tracker - Full-stack Flask app for managing RSUs, ISOs, ESPP grants with vesting schedules and tax tracking`
   - Choose Public or Private
   - **DO NOT** check any initialization options
   - Click "Create repository"

2. **Run the helper script:**
   ```bash
   ./push_to_github.sh
   ```
   
3. **Follow the prompts:**
   - Enter your GitHub username
   - Confirm you've created the repository
   - The script will push your code

## Method 2: Manual Commands

1. **Create the repository on GitHub** (same as above)

2. **Run these commands** (replace YOUR_USERNAME):
   ```bash
   # Add the remote
   git remote add origin https://github.com/YOUR_USERNAME/stonks.git
   
   # Rename branch to main
   git branch -M main
   
   # Push to GitHub
   git push -u origin main
   ```

## Method 3: Using GitHub CLI (If Installed)

```bash
# Install GitHub CLI (if needed)
brew install gh

# Login to GitHub
gh auth login

# Create and push repository
gh repo create stonks --public --source=. --remote=origin --push
```

## Authentication Options

If you get authentication errors, you have three options:

### Option A: GitHub CLI (Recommended)
```bash
brew install gh
gh auth login
```

### Option B: SSH Key
1. Generate SSH key: `ssh-keygen -t ed25519 -C "your_email@example.com"`
2. Add to GitHub: https://github.com/settings/keys
3. Use SSH URL: `git@github.com:YOUR_USERNAME/stonks.git`

### Option C: Personal Access Token
1. Create token: https://github.com/settings/tokens/new
   - Select scopes: `repo` (all)
   - Copy the token
2. When pushing, use token as password
3. Or configure credential helper:
   ```bash
   git config --global credential.helper osxkeychain
   ```

## After Pushing

1. **View your repository:**
   https://github.com/YOUR_USERNAME/stonks

2. **Add repository metadata:**
   - Description: "SpaceX Stock Compensation Tracker - Full-stack Flask app for managing RSUs, ISOs, ESPP grants with vesting schedules and tax tracking"
   - Topics: `python`, `flask`, `stock-tracker`, `spacex`, `rsu`, `iso`, `espp`, `vesting`, `webapp`
   - Website: (your deployment URL if you deploy it)

3. **Repository settings to consider:**
   - Enable Issues for bug tracking
   - Enable Discussions for community Q&A
   - Set up branch protection for `main` branch
   - Add collaborators if team project

4. **Optional enhancements:**
   - Add GitHub Actions for CI/CD
   - Add badges to README (build status, license, etc.)
   - Set up GitHub Pages for documentation
   - Enable Dependabot for security updates

## Troubleshooting

### Error: "repository not found"
- Make sure you created the repository on GitHub first
- Check the repository name is exactly `stonks`
- Verify you have access to the repository

### Error: "authentication failed"
- Use GitHub CLI: `gh auth login`
- Or set up SSH keys
- Or use a personal access token

### Error: "remote origin already exists"
```bash
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/stonks.git
```

### Error: "failed to push some refs"
- Don't initialize the GitHub repo with README/gitignore
- If you did, force push: `git push -u origin main --force` (only for new repos!)

## Current Repository Status

```bash
# Check what's committed
git log --oneline -10

# Check git status
git status

# View configured remotes
git remote -v

# See all branches
git branch -a
```

## What's Being Pushed

- ✅ Application code (app/, main.py)
- ✅ Configuration (requirements.txt, Procfile, Dockerfile)
- ✅ Documentation (README.md, all markdown docs)
- ✅ Templates and static files
- ✅ Database utilities (without database file)
- ✅ .gitignore to protect sensitive data
- ❌ Virtual environment (excluded)
- ❌ Database file (excluded)
- ❌ Cache files (excluded)
- ❌ Environment variables (excluded)

## Security Checklist

- [x] .gitignore includes sensitive files
- [x] Database file is excluded
- [x] No hardcoded passwords or API keys
- [x] Virtual environment is excluded
- [x] __pycache__ directories are excluded
- [x] .env files are excluded (if you add them later)

---

**Ready to push?** Run `./push_to_github.sh` and follow the prompts!
