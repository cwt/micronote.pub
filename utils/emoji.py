import emoji


def flexmoji(html):
    html = emoji.emojize(html, language="alias")
    html = emoji.emojize(html, language="alias", delimiters=(':blob_', ':'))
    html = emoji.emojize(html, language="alias", delimiters=(':blob', ':'))
    return html
