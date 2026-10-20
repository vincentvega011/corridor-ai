"""Subsets Noto Sans SC (SIL OFL 1.1) to the characters the zh-CN UI uses plus common banking characters.

    uv run --with fonttools --with brotli python scripts/subset_zh_font.py /path/to/NotoSansSC[wght].ttf

Source font: https://github.com/google/fonts/tree/main/ofl/notosanssc (variable, wght 100 to 900). The weight axis is
limited to 400 to 700 and the glyph set is cut to the used characters, so the committed file stays small.
Output: frontend/app/fonts/noto-sans-sc-subset-wght.woff2. Run again after changing zh strings.
"""
import json
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
MSG = ROOT / "frontend" / "messages"
# common banking and everyday characters beyond the strings in use, so small copy edits do not need a new font
EXTRA = (
    "银行账户转账汇款收款付款余额明细交易记录金额币种汇率手续费到账时间日期年月日时分秒"
    "学费房租账单分期预约提醒限额额度风险验证码身份认证密码安全确认取消提交保存删除修改查询设置"
    "信用护照证明声明签名授权有效无效过期失败成功完成处理中等待暂缓冻结拒绝允许同意审核核实核验"
    "姓名地址电话邮箱手机号国家地区城市中国大陆香港澳门台湾港澳台人民币美元澳元卡塔尔里亚尔尼泊尔卢比林吉特"
    "客户用户家人父母兄弟姐妹亲属收入支出储蓄消费贷款利息本金逾期还款提现充值扣款退款"
    "电子凭证文件上传下载附件扫描照片图片发票合同协议条款隐私政策服务提供商渠道路线"
    "一二三四五六七八九十百千万亿零〇第个次笔项条张份套类种每各全部其他更多更少"
    "《》“”‘’、。，；：！？（）【】—…·￥￡€"
)  # Latin letters, digits and ASCII punctuation come from IBM Plex Sans, so they are not in this file
chars = set(EXTRA)
for name in ("zh.json", "console.zh.json"):
    for v in json.loads((MSG / name).read_text()).values():
        chars.update(c for c in v if ord(c) > 127)
for item in json.loads((MSG / "server.zh.json").read_text()):
    chars.update(c for c in item["zh"] if ord(c) > 127)
src = Path(sys.argv[1])
font = TTFont(src)
opts = subset.Options()
opts.flavor = "woff2"
opts.layout_features = ["kern", "liga", "ccmp", "locl", "vert", "vrt2"]
opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
opts.notdef_outline = True
opts.hinting = False
sub = subset.Subsetter(opts)
sub.populate(text="".join(sorted(chars)))
sub.subset(font)
font = instancer.instantiateVariableFont(font, {"wght": (400, 700)})  # after the cut: far fewer glyphs to instantiate
out = ROOT / "frontend" / "app" / "fonts" / "noto-sans-sc-subset-wght.woff2"
subset.save_font(font, str(out), opts)
print(len(chars), "characters,", out.stat().st_size, "bytes")
