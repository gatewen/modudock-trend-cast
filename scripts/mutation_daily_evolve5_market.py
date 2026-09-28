from scripts import mutation_check as runner
P='back/market_features.py';T='tests.test_market.'
runner.MUTATIONS=[
 ('missing_code_marked_available',P,[("info['status']='missing_code'", "info['status']='available'")],T+'MarketFeatureTests.test_missing_latest_never_backfills'),
 ('fx_sell_missing_ignored',P,[("keys=('spot_buy','spot_sell')", "keys=('spot_buy',)")],T+'MarketFeatureTests.test_audit_missing_code_changes_no_rows_features_or_hashes'),
 ('missing_quote_rows_removed',P,[("return self.rows[series][:i+1],info", "return (self.rows[series][:i+1] if info['status']!='missing_code' else []),info")],T+'MarketFeatureTests.test_missing_latest_never_backfills'),
 ('repair_changes_input_hash','scripts/repair_market_alignment.py',[("UPDATE market_features SET alignment_json=? WHERE", "UPDATE market_features SET input_hash=upper(input_hash),alignment_json=? WHERE")],T+'MarketPersistenceTests.test_alignment_repair_changes_only_audit_json_and_rejects_other_differences'),
 ('repair_accepts_other_audit_changes','scripts/repair_market_alignment.py',[("if actual!=legacy:raise DataError", "if False:raise DataError")],T+'MarketPersistenceTests.test_alignment_repair_changes_only_audit_json_and_rejects_other_differences'),
]
if __name__=='__main__':raise SystemExit(runner.main())
