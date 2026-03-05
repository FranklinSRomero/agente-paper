CREATE DATABASE IF NOT EXISTS products;
USE products;

CREATE TABLE IF NOT EXISTS products_catalog (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(64) NOT NULL UNIQUE,
  barcode VARCHAR(32) NULL UNIQUE,
  product_name VARCHAR(255) NOT NULL,
  categoria VARCHAR(100) NULL,
  price DECIMAL(10,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO products_catalog (sku, barcode, product_name, categoria, price, stock)
VALUES
  ('SKU-LACT-001', '7501000000001', 'Leche deslactosada 1L', 'lacteos', 2.35, 18),
  ('SKU-LACT-002', '7501000000002', 'Leche deslactosada light 1L', 'lacteos', 2.49, 4),
  ('SKU-LACT-003', '7501000000003', 'Leche entera 1L', 'lacteos', 2.10, 26),
  ('SKU-CERE-010', '7502000000010', 'Cereal avena integral 500g', 'despensa', 3.80, 11),
  ('SKU-SNCK-021', '7503000000021', 'Barra proteica chocolate', 'snacks', 1.95, 0),
  ('SKU-AX21', NULL, 'Repuesto filtro AX21', 'hogar', 12.90, 7)
ON DUPLICATE KEY UPDATE
  product_name = VALUES(product_name),
  categoria = VALUES(categoria),
  price = VALUES(price),
  stock = VALUES(stock);

CREATE TABLE IF NOT EXISTS sales_transactions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  tx_ref VARCHAR(80) NOT NULL UNIQUE,
  sale_date DATE NOT NULL,
  sale_ts DATETIME NOT NULL,
  sku VARCHAR(64) NOT NULL,
  barcode VARCHAR(32) NULL,
  product_name VARCHAR(255) NOT NULL,
  categoria VARCHAR(100) NULL,
  concepto VARCHAR(40) NOT NULL,
  sales_channel VARCHAR(40) NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  net_amount DECIMAL(12,2) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_sales_date (sale_date),
  INDEX idx_sales_sku (sku),
  INDEX idx_sales_cat (categoria),
  INDEX idx_sales_concept (concepto)
);

INSERT INTO sales_transactions (
  tx_ref,
  sale_date,
  sale_ts,
  sku,
  barcode,
  product_name,
  categoria,
  concepto,
  sales_channel,
  quantity,
  unit_price,
  discount_pct,
  net_amount
)
WITH RECURSIVE day_span AS (
  SELECT DATE_SUB(CURDATE(), INTERVAL 34 DAY) AS d
  UNION ALL
  SELECT DATE_ADD(d, INTERVAL 1 DAY) FROM day_span WHERE d < CURDATE()
),
base AS (
  SELECT
    ds.d AS sale_date,
    p.sku,
    p.barcode,
    p.product_name,
    p.categoria,
    p.price,
    ((DAYOFMONTH(ds.d) + LENGTH(p.sku)) % 4) + 1 AS qty_seed,
    ((DAYOFYEAR(ds.d) + LENGTH(p.product_name)) % 11) AS mod_seed
  FROM day_span ds
  JOIN products_catalog p
    ON p.sku IN ('SKU-LACT-001', 'SKU-LACT-002', 'SKU-LACT-003', 'SKU-CERE-010', 'SKU-SNCK-021')
)
SELECT
  CONCAT('TX-', DATE_FORMAT(sale_date, '%Y%m%d'), '-', REPLACE(sku, '-', ''), '-', LPAD(mod_seed, 2, '0')) AS tx_ref,
  sale_date,
  TIMESTAMP(sale_date, MAKETIME(8 + (mod_seed % 10), (mod_seed * 7) % 60, 0)) AS sale_ts,
  sku,
  barcode,
  product_name,
  categoria,
  CASE
    WHEN mod_seed IN (0, 10) THEN 'devolucion'
    WHEN mod_seed IN (3, 6) THEN 'promocion'
    ELSE 'venta'
  END AS concepto,
  CASE mod_seed % 3
    WHEN 0 THEN 'tienda_fisica'
    WHEN 1 THEN 'telegram'
    ELSE 'domicilio'
  END AS sales_channel,
  CASE
    WHEN mod_seed IN (0, 10) THEN -1
    ELSE qty_seed
  END AS quantity,
  price AS unit_price,
  CASE
    WHEN mod_seed IN (3, 6) THEN 0.15
    ELSE 0.00
  END AS discount_pct,
  ROUND(
    (CASE WHEN mod_seed IN (0, 10) THEN -1 ELSE qty_seed END)
    * price
    * (1 - (CASE WHEN mod_seed IN (3, 6) THEN 0.15 ELSE 0.00 END)),
    2
  ) AS net_amount
FROM base
ON DUPLICATE KEY UPDATE
  sale_date = VALUES(sale_date),
  sale_ts = VALUES(sale_ts),
  barcode = VALUES(barcode),
  product_name = VALUES(product_name),
  categoria = VALUES(categoria),
  concepto = VALUES(concepto),
  sales_channel = VALUES(sales_channel),
  quantity = VALUES(quantity),
  unit_price = VALUES(unit_price),
  discount_pct = VALUES(discount_pct),
  net_amount = VALUES(net_amount);

CREATE USER IF NOT EXISTS 'readonly_bot'@'%' IDENTIFIED BY 'readonly_password';
ALTER USER 'readonly_bot'@'%' IDENTIFIED BY 'readonly_password';
GRANT SELECT ON products.* TO 'readonly_bot'@'%';
FLUSH PRIVILEGES;
