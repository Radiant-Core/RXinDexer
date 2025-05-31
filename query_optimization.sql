-- Create query optimization views and functions to redirect slow queries to use materialized view

-- Create a view to intercept common query patterns and redirect them
CREATE OR REPLACE VIEW optimized_address_balances AS
SELECT address, total_balance as balance 
FROM address_balances;

-- Create a performance optimization function to redirect slow balance queries
CREATE OR REPLACE FUNCTION get_address_balances(min_balance NUMERIC DEFAULT 0)
RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
BEGIN
    RETURN QUERY
    SELECT ab.address, ab.total_balance
    FROM address_balances ab
    WHERE ab.total_balance > min_balance;
END;
$$ LANGUAGE plpgsql;

-- Create specific function for the large balance query pattern we're seeing
CREATE OR REPLACE FUNCTION get_large_balances(threshold NUMERIC DEFAULT 1000000000)
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    RETURN QUERY
    SELECT ab.address, ab.total_balance
    FROM address_balances ab
    WHERE ab.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;

-- Create a utility function to force refresh before important queries
CREATE OR REPLACE FUNCTION get_fresh_large_balances(threshold NUMERIC DEFAULT 1000000000)
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    -- First refresh the materialized view
    PERFORM safe_refresh_address_balances();
    
    -- Then return the results
    RETURN QUERY
    SELECT ab.address, ab.total_balance
    FROM address_balances ab
    WHERE ab.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;
