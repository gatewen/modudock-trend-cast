#!/usr/local/bin/python3
from scripts import mutation_check
K='back/keychain.py';N='back/news_digest.py';R='back/runtime.py';S='back/daily_forward_service.py'
T='tests.test_keychain.KeychainTests.';V='tests.test_news_digest.NewsValidationTests.';D='tests.test_news_digest.NewsSnapshotTests.'
P='tests.test_news_digest.NewsProtocolTests.test_real_ndjson_subscription_route_drop_invalid_and_keep_running_without_news'
MUTATIONS=[
 ('environment_priority_removed',K,[("if os.environ.get(name):continue","if False:continue")],T+'test_fake_security_exact_argv_success_env_priority_and_cleanup'),
 ('platform_guard_removed',K,[("if (sys.platform if platform is None else platform) != 'darwin':return None","if False:return None")],T+'test_default_absolute_path_nonmac_and_absent_account_skip'),
 ('security_timeout_removed',K,[("timeout=5)","timeout=None)")],T+'test_default_absolute_path_nonmac_and_absent_account_skip'),
 ('security_stderr_exposed',K,[("stderr=subprocess.DEVNULL","stderr=None")],T+'test_cli_failure_safe_and_no_child_stderr_in_process_logs'),
 ('failed_exit_accepted',K,[("if result.returncode != 0:return None","if False:return None")],T+'test_missing_executable_failed_exit_empty_and_malformed_password_skip'),
 ('lookup_failure_not_caught',K,[("except (OSError, UnicodeError, subprocess.SubprocessError):","except UnicodeError:")],T+'test_missing_executable_failed_exit_empty_and_malformed_password_skip'),
 ('credential_cleanup_removed',K,[("for name, old in previous.items():","for name, old in []:")],T+'test_both_services_and_empty_env_restored_even_on_exception'),
 ('password_printed',K,[("return value\n    except","print(value)\n        return value\n    except")],T+'test_cli_uses_fallback_in_memory_then_restores_on_service_error'),
 ('digest_size_doubled',N,[("MAX_BYTES = 8 * 1024","MAX_BYTES = 16 * 1024")],V+'test_wire_utf8_size_includes_whitespace_escapes_and_exact_limit'),
 ('wire_bytes_ignored',N,[("(wire_size is not None and wire_size>MAX_BYTES)","False")],V+'test_wire_utf8_size_includes_whitespace_escapes_and_exact_limit'),
 ('schema_version_unchecked',N,[("or body['schema']!=1"," ")],V+'test_strict_schema_types_nested_allowlist_and_no_links'),
 ('extra_fields_allowed',N,[("set(body)!=FIELDS","not FIELDS.issubset(body)")],V+'test_strict_schema_types_nested_allowlist_and_no_links'),
 ('boolean_counts_accepted',N,[("type(value) is int","isinstance(value,int)")],V+'test_strict_schema_types_nested_allowlist_and_no_links'),
 ('eleven_themes_allowed',N,[("len(themes)>10","len(themes)>11")],V+'test_strict_schema_types_nested_allowlist_and_no_links'),
 ('duplicate_json_keys_allowed',N,[("if k in result:raise ValueError('duplicate key')","if False:raise ValueError('duplicate key')")],V+'test_duplicate_keys_and_nonfinite_rejected'),
 ('future_publication_allowed',N,[("or at>received_at"," ")],V+'test_strict_schema_types_nested_allowlist_and_no_links'),
 ('latest_replay_guard_removed',N,[("if old and old['at']>=value['at']:return False","if False:return False")],D+'test_replay_duplicate_and_out_of_order_do_not_change_receipt'),
 ('snapshot_receipt_cutoff_removed',N,[("and received<=cutoff:",":")],D+'test_late_receipt_prior_day_and_other_day_are_not_eligible'),
 ('snapshot_same_day_removed',N,[("at.date()==received.date() and ","")],D+'test_same_day_required_and_utc_cutoff_is_converted_to_taipei'),
 ('snapshot_after_close_overwritten',N,[("if at.date()==received.date() and at<=cutoff and received<=cutoff:","if True:")],D+'test_last_cutoff_snapshot_survives_afternoon_and_restart_latest_singleton'),
 ('calendar_promotion_removed',N,[("        _promote(store)\n        if store.db.execute", "        # mutation\n        if store.db.execute")],D+'test_unknown_calendar_candidate_promoted_only_when_confirmed'),
 ('latest_used_instead_of_snapshot',N,[("digest=snapshot(store,day)","digest=json.loads(store.db.execute('SELECT payload_json FROM news_digests WHERE id=1').fetchone()[0])")],D+'test_late_receipt_prior_day_and_other_day_are_not_eligible'),
 ('p5_flag_disabled',N,[("JEV_NEWS_ENABLED = True","JEV_NEWS_ENABLED = False")],'tests.test_news_forward.NewsForwardTests.test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('p5_metadata_sent',N,[("if k!='at'","if True")],D+'test_p5_disabled_preview_is_separate_immutable_and_contains_only_daily_and_snapshot'),
 ('news_topic_ignored',R,[("self.closed or topic != news_digest.TOPIC","self.closed")],'tests.test_news_digest.NewsProtocolTests.test_topic_and_validation_before_queue_receipt_is_local_and_queue_bounded'),
 ('news_route_disconnected','back/trendcast.py',[("app.event(packet.get('topic'), packet.get('body'), wire_size=body_size)","pass")],P),
 ('news_service_record_removed',S,[("self._write(lambda s:record_news_input(s,day,self.now()))","pass")], 'tests.test_news_digest.NewsServiceTests.test_existing_forward_service_records_missing_or_ready_news_without_extra_jev'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
