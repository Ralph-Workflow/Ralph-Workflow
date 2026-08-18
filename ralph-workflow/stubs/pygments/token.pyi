"""Minimal Pygments token surface used by Ralph's syntax themes.

Real pygments ``_TokenType`` subclasses ``tuple`` (token types are tuple
chains like ``Token.Name.Function``), so the stub models it as a
``tuple[str, ...]`` subclass to stay assignment-compatible with rich's
``SyntaxTheme.get_style_for_token(token_type: tuple[str, ...])``.
"""


class TokenType(tuple[str, ...]):
    Function: TokenType
    Whitespace: TokenType
    Subheading: TokenType
    Deleted: TokenType
    Inserted: TokenType

Comment: TokenType
Generic: TokenType
Keyword: TokenType
Name: TokenType
Number: TokenType
Operator: TokenType
Punctuation: TokenType
String: TokenType
Text: TokenType
