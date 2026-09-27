"""Predeclared daily research protocol; never tuned against reported results."""
from .daily_replay import DEV_START, DEV_END, HOLD_START, HOLD_END

INDICATORS = ('ma_cross', 'ma_trend', 'rsi14', 'kd', 'macd', 'bollinger',
              'bias20', 'vol_price', 'foreign_net', 'trust_net', 'margin_chg')
BASELINES = ('always_flat', 'majority', 'momentum_H', 'reversal_H', 'vol_prior_d')
METHODS = BASELINES + tuple('ind_'+name for name in INDICATORS) + ('ind_logit',)
LABELS = ('up', 'flat', 'down')
TIE_ORDER = ('flat', 'up', 'down')
STATES = {
    'ma_cross': ('bull', 'bear', 'neutral'),
    'ma_trend': ('bull', 'bear', 'neutral'),
    'rsi14': ('oversold', 'overbought', 'middle'),
    'kd': ('bull_cross', 'bear_cross', 'low', 'middle', 'high'),
    'macd': ('bull_flip', 'bear_flip', 'positive', 'negative', 'zero'),
    'bollinger': ('above', 'below', 'inside'),
    'bias20': ('low', 'middle', 'high'),
    'vol_price': ('up_high', 'up_low', 'down_high', 'down_low', 'flat_high', 'flat_low'),
    'foreign_net': ('positive', 'negative', 'zero'),
    'trust_net': ('positive', 'negative', 'zero'),
    'margin_chg': ('positive', 'negative', 'zero'),
}
NUMERIC = ('ma_cross', 'ma_trend', 'rsi14', 'kd_k', 'kd_d', 'macd_dif',
           'macd_signal', 'macd_hist', 'bollinger', 'bias20', 'vol_price_return',
           'vol_price_ratio', 'foreign_net', 'trust_net', 'margin_chg')
CLAIMS = {
    'ma_cross': {'bull': '黃金交叉偏多', 'bear': '死亡交叉偏空'},
    'ma_trend': {'bull': '站上長均線偏多', 'bear': '跌破長均線偏空'},
    'rsi14': {'oversold': '超賣可能反彈', 'overbought': '超買可能回落'},
    'kd': {'bull_cross': '低檔黃金交叉偏多', 'bear_cross': '高檔死亡交叉偏空'},
    'macd': {'bull_flip': '柱狀體翻正偏多', 'bear_flip': '柱狀體翻負偏空'},
    'bollinger': {'above': '上軌突破（追勢／反轉說法並存）', 'below': '下軌跌破（追勢／反轉說法並存）'},
    'bias20': {'low': '低乖離可能反彈', 'high': '高乖離可能回落'},
    'vol_price': {'up_high': '價漲量增偏多', 'down_high': '價跌量增偏空'},
    'foreign_net': {'positive': '外資買超偏多', 'negative': '外資賣超偏空'},
    'trust_net': {'positive': '投信買超偏多', 'negative': '投信賣超偏空'},
    'margin_chg': {'positive': '融資增加（追勢／反向說法並存）', 'negative': '融資減少（追勢／反向說法並存）'},
}

# Values are implementation definitions, not fitted hyperparameters.
PROTOCOL = {
    'version': 'daily-indicators-v1', 'indicators': INDICATORS, 'methods': METHODS,
    'numeric_columns': NUMERIC, 'states': STATES, 'claims': CLAIMS,
    'warmup': 60, 'horizons': [3, 7, 14], 'chip_lag_sessions': 1,
    'ma_cross': {'fast': 5, 'slow': 20, 'lookback': 3, 'multiple': 'latest'},
    'ma_trend': {'window': 60, 'equal': 'neutral'},
    'rsi14': {'window': 14, 'smoothing': 'Wilder', 'initial': 'mean_first_14', 'flat': 50},
    'kd': {'rsv_window': 9, 'k_smoothing': 3, 'd_smoothing': 3, 'initial': 50, 'flat_rsv': 50},
    'macd': {'fast': 12, 'slow': 26, 'signal': 9, 'ema_initial': 'first_value', 'histogram': 'dif_minus_signal'},
    'bollinger': {'window': 20, 'sigma': 2, 'ddof': 0}, 'bias20': {'window': 20},
    'vol_price': {'window': 20, 'mean_includes_today': True, 'flat': 'separate'},
    'foreign_net': {'sum_sessions': 3}, 'trust_net': {'sum_sessions': 3},
    'margin_chg': {'difference_sessions': 5},
    'vol_prior_d': {'return_count': 20, 'ddof': 0},
    'frequency': {'min_samples': 30, 'add': 1, 'fallback': 'majority', 'tie': TIE_ORDER},
    'terciles': {'quantiles': [1/3, 2/3], 'interpolation': 'linear', 'equal': 'lower', 'fit': 'matured_dev_only'},
    'k': {'sigma_multiplier': .5, 'ddof': 0, 'step': '.005', 'rounding': 'HALF_UP', 'endpoints': 'both_dev'},
    'logit': {'lambda': 1., 'steps': 300, 'learning_rate': .1, 'refit_sessions': 20,
              'min_train': 250, 'initial': 'zero_every_fit', 'loss': 'mean_CE_plus_lambda_over_2_L2',
              'penalize_intercept': False, 'scale': 'train_population',
              'missing': 'train_mean_and_missing_state', 'onehot_scale': False,
              'numeric_zero_sd': 1., 'refit_origin': 'first_predictable_dev_session',
              'bias_state_cuts': 'fit_training_only_at_refit'},
    'bootstrap': {'block_sessions': 20, 'layout': 'nonoverlapping_keep_tail', 'iterations': 2000, 'seed': 20260927},
}


def settings(start='2010-01-04'):
    return dict(symbol='2330', start=start, dev_start=DEV_START, dev_end=DEV_END,
                hold_start=HOLD_START, hold_end=HOLD_END, protocol=PROTOCOL)
