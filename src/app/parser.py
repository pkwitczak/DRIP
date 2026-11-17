import ujson


class JsonParser:
    def parse(self, data: bytes) -> dict:
        return ujson.loads(data)


class ParserFactory:
    def __init__(self):
        self._parsers = {
            "application/json": JsonParser(),
            "text/json": JsonParser(),
        }

    def register(self, content_type: str, parser):
        self._parsers[content_type] = parser

    def get(self, content_type: str):
        return self._parsers.get(content_type)

    def parse(self, data: bytes, content_type: str = "text/json"):
        parser = self.get(content_type)
        if parser is None:
            raise ValueError("No parser for content_type: " + str(content_type))

        return parser.parse(data)