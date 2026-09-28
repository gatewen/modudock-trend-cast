// Presentation only; protocol values and data-method identifiers stay unchanged.
export const METHOD_NAMES = {
  always_flat:'永遠猜盤整', majority:'猜最常見答案', momentum_H:'順勢預測', reversal_H:'反向預測',
  vol_prior_d:'依波動度猜答案', ind_ma_cross:'5／20 日均線交叉', ind_ma_trend:'60 日均線趨勢',
  ind_rsi14:'RSI', ind_kd:'KD', ind_macd:'MACD', ind_bollinger:'布林通道', ind_bias20:'20 日乖離',
  ind_vol_price:'價量', ind_foreign_net:'外資買賣超', ind_trust_net:'投信買賣超', ind_margin_chg:'融資變化',
  ind_logit:'指標組合模型', ens_avg:'組合預測', ind_mkt_trend:'大盤均線趨勢', ind_mkt_ret5:'大盤 5 日報酬',
  ind_adr_premium:'台積電 ADR 溢價', ind_sox_ret1:'半導體指數單日報酬', ind_sox_trend:'半導體指數均線趨勢',
  mkt_logit:'大環境組合模型', jev_ind:'jev 讀指標', jev_news:'jev 讀新聞',
};
const pattern = new RegExp(`\\b(${Object.keys(METHOD_NAMES).join('|')})\\b`,'g');
export const plainMethods = text => String(text).replace(pattern,code=>`${METHOD_NAMES[code]}（${code}）`);
export function writeMethods(node,text) {
  const doc=node.ownerDocument;
  node.replaceChildren();
  for(const token of String(text).split(pattern)) {
    if(Object.hasOwn(METHOD_NAMES,token)) {
      const name=doc.createElement('span');name.className='tc-method';name.dataset.method=token;
      name.append(doc.createTextNode(METHOD_NAMES[token]));
      const code=doc.createElement('small');code.className='tc-method-code';code.textContent=`（${token}）`;
      name.append(code);node.append(name);
    } else node.append(doc.createTextNode(token));
  }
}
