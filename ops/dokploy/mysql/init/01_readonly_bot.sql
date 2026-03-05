CREATE USER IF NOT EXISTS 'readonly_bot'@'%' IDENTIFIED BY 'readonly_password';
ALTER USER 'readonly_bot'@'%' IDENTIFIED BY 'readonly_password';
GRANT SELECT ON products.* TO 'readonly_bot'@'%';
FLUSH PRIVILEGES;

