# Sansa Learn - Student Management System

A comprehensive web application for managing student records and monthly fee tracking for Sansa Learn coaching institute.

## Features

- **Student Management**
  - Add, edit, and delete student records
  - Upload student photos
  - Store complete student information (personal, academic, contact details)
  - Auto-generated unique admission numbers
  - Search by admission number, student name, or father's name

- **Fee Management**
  - Track monthly fee payments
  - Mark fees as paid/unpaid
  - Record payment date, mode, and remarks
  - Auto-calculate total paid and pending amounts
  - View complete fee history

- **PDF Generation**
  - Generate professional fee receipts with Sansa Learn branding
  - Generate demand bills for unpaid fees
  - Include institute logo, address, and signature placeholder

- **Reports & Export**
  - Export student data to CSV
  - Export fee records to CSV
  - Dashboard with key statistics

## Tech Stack

- **Backend**: Python 3.11, Flask
- **Database**: SQLite3
- **PDF Generation**: ReportLab
- **Image Processing**: Pillow
- **Frontend**: Bootstrap 5, HTML/CSS/JavaScript

## Installation

The project is pre-configured for Replit. Simply click "Run" to start the application.

## Usage

1. **Dashboard**: View statistics and quick search
2. **Add Student**: Register new students with complete details
3. **View Students**: Browse, search, and manage student records
4. **Fee Management**: Track and record monthly fee payments
5. **Generate PDFs**: Create receipts and demand bills

## Institute Information

- **Name**: Sansa Learn
- **Address**: Chandmari Road Kankarbagh gali no. 06 ke thik saamne
- **Contact**: 9153021229, 7488039012

## Database Structure

- **students**: Student personal, academic, and contact information
- **fees**: Monthly fee payment records
- **institute_info**: Institute branding and contact details

## Security

- Uses SESSION_SECRET for Flask session management
- Secure file upload handling
- SQL injection protection via parameterized queries

## Author

Built for Sansa Learn coaching institute.
