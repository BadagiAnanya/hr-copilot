-- Seed data: fake employees + leave balances for demo and evals.
-- Run after docker/init.sql has created the schema:
--   psql $POSTGRES_URL -f data/seed/employees.sql

TRUNCATE employees, leave_balances CASCADE;

INSERT INTO employees (employee_id, slack_user_id, name, email, department, location, manager_id, joined_at, acl_groups) VALUES
('E1024', 'U_DEMO_ANANYA',  'Ananya Badagi',   'ananya@example.com',   'engineering',  'bangalore', 'E2001', '2024-08-01', ARRAY['all_employees','engineering']),
('E1025', 'U_DEMO_RAHUL',   'Rahul Mehta',     'rahul@example.com',    'engineering',  'bangalore', 'E2001', '2023-03-15', ARRAY['all_employees','engineering']),
('E1026', 'U_DEMO_PRIYA',   'Priya Nair',      'priya@example.com',    'product',      'bangalore', 'E2002', '2022-11-01', ARRAY['all_employees','product']),
('E1027', 'U_DEMO_SAM',     'Sam Rodrigues',   'sam@example.com',      'design',       'mumbai',    'E2003', '2025-01-10', ARRAY['all_employees','design']),
('E1028', 'U_DEMO_ARJUN',   'Arjun Shah',      'arjun@example.com',    'finance',      'mumbai',    'E2004', '2021-06-01', ARRAY['all_employees','finance']),
('E2001', 'U_DEMO_MGR1',    'Deepa Krishnan',  'deepa@example.com',    'engineering',  'bangalore', NULL,    '2019-04-01', ARRAY['all_employees','engineering','managers']),
('E2002', 'U_DEMO_MGR2',    'Vikram Rao',      'vikram@example.com',   'product',      'bangalore', NULL,    '2018-09-01', ARRAY['all_employees','product','managers']),
('E2003', 'U_DEMO_MGR3',    'Neha Jain',       'neha@example.com',     'design',       'mumbai',    NULL,    '2020-02-01', ARRAY['all_employees','design','managers']),
('E2004', 'U_DEMO_MGR4',    'Kiran Patel',     'kiran@example.com',    'finance',      'mumbai',    NULL,    '2017-07-01', ARRAY['all_employees','finance','managers']);

INSERT INTO leave_balances (employee_id, year, leave_type, allotted, used) VALUES
('E1024', 2026, 'sick',   12, 3),
('E1024', 2026, 'casual', 10, 2),
('E1024', 2026, 'earned', 21, 5),
('E1025', 2026, 'sick',   12, 7),
('E1025', 2026, 'casual', 10, 5),
('E1025', 2026, 'earned', 21, 12),
('E1026', 2026, 'sick',   12, 0),
('E1026', 2026, 'casual', 10, 10),
('E1026', 2026, 'earned', 21, 3),
('E1027', 2026, 'sick',   12, 1),
('E1027', 2026, 'casual', 10, 1),
('E1027', 2026, 'earned', 21, 0),
('E1028', 2026, 'sick',   12, 4),
('E1028', 2026, 'casual', 10, 6),
('E1028', 2026, 'earned', 21, 8);
