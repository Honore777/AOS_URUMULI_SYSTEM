-- Calculate moyenne and nobelium for the selected stocks
SELECT 
    SUM((percentage / 100.0) * 14.40) AS weighted_1009,
    SUM((percentage / 100.0) * 9.50) AS weighted_1025,
    SUM((percentage / 100.0) * 19.90) AS weighted_1017,
    SUM((percentage / 100.0) * 8.90) AS weighted_1018,
    SUM((percentage / 100.0) * 5.70) AS weighted_1029,
    SUM((percentage / 100.0) * 1.40) AS weighted_1056,
    SUM((percentage / 100.0) * 4.90) AS weighted_993,
    SUM((percentage / 100.0) * 5.90) AS weighted_961,
    SUM((percentage / 100.0) * 60.30) AS weighted_1042,
    SUM((percentage / 100.0) * 2.40) AS weighted_1000,
    SUM((percentage / 100.0) * 9.90) AS weighted_1089,
    SUM((percentage / 100.0) * 13.90) AS weighted_912,
    SUM((percentage / 100.0) * 10.40) AS weighted_1007,
    SUM((percentage / 100.0) * 2.40) AS weighted_1005,
    SUM((percentage / 100.0) * 5.90) AS weighted_674,
    SUM((percentage / 100.0) * 6.90) AS weighted_691,
    SUM((percentage / 100.0) * 6.90) AS weighted_725,
    SUM((percentage / 100.0) * 177.90) AS weighted_935,
    SUM((percentage / 100.0) * 12.40) AS weighted_947,
    SUM((percentage / 100.0) * 12.40) AS weighted_956
FROM copper_stock
WHERE id IN (1009, 1025, 1017, 1018, 1029, 1056, 993, 961, 1042, 1000, 1089, 912, 1007, 1005, 674, 691, 725, 935, 947, 956);
