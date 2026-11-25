// rotm-stamp: contract sha256:faec27f3b5574f095e80c760c494156ab84ae6dd8793e5fc01c8575f87b5314c; body sha256:b0f50914bae2496dfea3b7de0e0f34b98a3c9973fc0b86c3794d75b5a38489b1
let
    // rule-of-thumb-model: Power Query for raw table 'lines' (contracts/lines.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\lines.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"line_id", "line_date", "category", "amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"line_id", "line_date", "category", "amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"line_id", type text}, {"line_date", type date}, {"category", type text}, {"amount", type number}}, "en-US")
in
    Typed
