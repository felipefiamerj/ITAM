-- FIAME System
-- Script de apoio para ambientes MySQL.
-- Este projeto está configurado para usar o banco itam_db.

CREATE DATABASE IF NOT EXISTS itam_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Crie um usuario dedicado fora do repositorio, usando uma senha forte.
-- Exemplo, ajuste antes de executar:
-- CREATE USER IF NOT EXISTS 'itam'@'localhost' IDENTIFIED BY '<senha_forte_aqui>';
-- GRANT ALL PRIVILEGES ON itam_db.* TO 'itam'@'localhost';
-- FLUSH PRIVILEGES;

-- Depois de configurar o .env:
-- DB_ENGINE=django.db.backends.mysql
-- DB_NAME=itam_db
-- DB_USER=itam
-- DB_PASSWORD=<senha_forte_aqui>
-- Execute:
-- python manage.py migrate
-- python manage.py createsuperuser
