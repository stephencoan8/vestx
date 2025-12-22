#!/bin/bash

# GitHub Push Script for Stonks App
# This script will help you push your code to GitHub

echo "🚀 GitHub Setup for Stonks App"
echo "================================"
echo ""

# Check if we're in a git repository
if [ ! -d .git ]; then
    echo "❌ Error: Not a git repository"
    exit 1
fi

# Get the GitHub username and repo name
echo "Please enter your GitHub username:"
read GITHUB_USERNAME

if [ -z "$GITHUB_USERNAME" ]; then
    echo "❌ Error: GitHub username is required"
    exit 1
fi

REPO_NAME="stonks"

echo ""
echo "📝 Summary:"
echo "  GitHub Username: $GITHUB_USERNAME"
echo "  Repository Name: $REPO_NAME"
echo "  Repository URL: https://github.com/$GITHUB_USERNAME/$REPO_NAME"
echo ""
echo "⚠️  Make sure you have created the repository on GitHub first!"
echo "   Go to: https://github.com/new"
echo "   - Repository name: $REPO_NAME"
echo "   - DO NOT initialize with README, .gitignore, or license"
echo ""
read -p "Have you created the repository on GitHub? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please create the repository first, then run this script again."
    exit 1
fi

# Add the remote
echo ""
echo "📡 Adding remote origin..."
git remote add origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"

if [ $? -ne 0 ]; then
    echo "⚠️  Remote already exists. Updating..."
    git remote set-url origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"
fi

# Verify the remote
echo ""
echo "✅ Remote configured:"
git remote -v

# Push to GitHub
echo ""
echo "📤 Pushing to GitHub..."
echo "  Branch: master -> main"
echo ""

# Rename branch to main if needed
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" = "master" ]; then
    echo "Renaming master branch to main..."
    git branch -M main
fi

# Push to GitHub
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Success! Your code has been pushed to GitHub!"
    echo ""
    echo "🌐 View your repository at:"
    echo "   https://github.com/$GITHUB_USERNAME/$REPO_NAME"
    echo ""
    echo "📖 Next steps:"
    echo "   1. Add a repository description on GitHub"
    echo "   2. Add topics/tags: python, flask, stock-tracker, spacex, rsu"
    echo "   3. Set up GitHub Pages (optional)"
    echo "   4. Configure branch protection rules (optional)"
    echo ""
else
    echo ""
    echo "❌ Error: Failed to push to GitHub"
    echo ""
    echo "Common issues:"
    echo "  1. Repository doesn't exist - create it at https://github.com/new"
    echo "  2. No write access - check your GitHub permissions"
    echo "  3. Authentication failed - you may need to set up a personal access token"
    echo ""
    echo "For authentication, you can:"
    echo "  - Use GitHub CLI: brew install gh && gh auth login"
    echo "  - Use SSH: https://docs.github.com/en/authentication/connecting-to-github-with-ssh"
    echo "  - Use Personal Access Token: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token"
fi
