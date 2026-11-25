// rotm-stamp: contract sha256:8498b10038384772cc814369b50538a6fda636bd0ebdadac63e5b4958d846e12; body sha256:e9646eb4bce1e8e031013e885d0b87859763b6b46f4d00a09f1caf9bbb7f2973
let
    // rule-of-thumb-model: Power Query for raw table 'sales' (contracts/sales.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\sales.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"invoice_id", "line_no", "inv_date", "region", "product", "amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"invoice_id", "line_no", "inv_date", "region", "product", "amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"invoice_id", type text}, {"line_no", Int64.Type}, {"inv_date", type date}, {"region", type text}, {"product", type text}, {"amount", type number}}, "en-US")
in
    Typed
