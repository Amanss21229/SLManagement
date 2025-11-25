# Sansa Learn Student Management System

## Project Overview
A complete full-stack web application for student data management and monthly fee tracking for Sansa Learn coaching institute. Built with Python Flask, SQLite database, and responsive Bootstrap UI.

## Purpose
Manage student records, track monthly fee payments, generate PDF receipts and demand bills with institute branding.

## Tech Stack
- Backend: Python 3.11 + Flask
- Database: SQLite3
- PDF Generation: ReportLab
- Image Processing: Pillow
- Frontend: Bootstrap 5, HTML/CSS/JS

## Project Structure
```
/
├── app.py                 # Main Flask application
├── database.db           # SQLite database (auto-created)
├── static/
│   ├── css/
│   │   └── style.css     # Custom styles
│   ├── js/              # JavaScript files
│   └── logo/
│       └── logo.png     # Sansa Learn logo
├── templates/           # HTML templates
│   ├── base.html       # Base template
│   ├── dashboard.html  # Main dashboard
│   ├── students.html   # Student list
│   ├── add_student.html
│   ├── edit_student.html
│   ├── view_student.html
│   ├── fee_management.html
│   └── student_fees.html
├── uploads/            # Student photos
└── pdfs/              # Generated PDF receipts

```

## Key Features
1. Complete CRUD for student records
2. Monthly fee tracking with paid/unpaid status
3. PDF receipt generation with institute branding
4. PDF demand bill for unpaid fees
5. Search by admission number, name, or father name
6. CSV export for students and fees
7. Mobile-responsive UI
8. Auto-generated admission numbers

## Database Schema
- **students**: id, admission_number, photo_path, name, father_name, mother_name, dob, gender, class, board, medium, school_name, address, mobile1, mobile2, fee_per_month, discount, admission_date, other_details
- **fees**: id, student_id, month, year, fee_amount, is_paid, payment_date, payment_mode, remarks
- **institute_info**: id, logo_path, address, contact, signature_path

## Recent Changes
- 2025-11-25: Initial project creation with complete student management and fee tracking system
- Integrated Sansa Learn logo (golden tree design) throughout the application
- Implemented PDF generation for receipts and demand bills

## User Preferences
- Single admin user (coaching institute owner)
- Simple, clean, mobile-friendly UI
- All data stored locally in SQLite

## Running the Application
The app runs on port 5000. Access via the Replit webview.
- Main route: / (dashboard)
- Students: /students
- Fee Management: /fees

## Institute Branding
- Name: Sansa Learn
- Logo: Golden tree design with books
- Address: Chandmari Road Kankarbagh gali no. 06 ke thik saamne
- Contact: 9153021229, 7488039012
