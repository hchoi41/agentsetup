// rotm-stamp: contract sha256:5a0d75bc1dc92d3bb27c43735848d3bf3d03d7065f674adf7f663c42571d0d46; body sha256:c5d25a89635e5a06b789b353b59c0d7a4c16080745d6076dbdc7a118cb85d0a6
let
    // rule-of-thumb-model: Power Query for raw table 'sales' (contracts/sales.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\sales.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"line_id", "period", "product", "qty", "revenue"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"line_id", "period", "product", "qty", "revenue"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"line_id", type text}, {"period", type text}, {"product", type text}, {"qty", Int64.Type}, {"revenue", type number}}, "en-US")
in
    Typed
