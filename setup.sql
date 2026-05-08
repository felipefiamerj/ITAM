-- ITAM System
-- Script de apoio para ambientes MySQL.
-- Este projeto está configurado para usar o banco itam_db.

CREATE DATABASE IF NOT EXISTS itam_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY 'f999030567F@';
GRANT ALL PRIVILEGES ON itam_db.* TO 'root'@'localhost';
FLUSH PRIVILEGES;

-- Depois de configurar o .env:
-- DB_ENGINE=django.db.backends.mysql
-- DB_NAME=itam_db
-- DB_USER=root
-- DB_PASSWORD=f999030567F@
-- Execute:
-- python manage.py migrate
-- python manage.py createsuperuser
