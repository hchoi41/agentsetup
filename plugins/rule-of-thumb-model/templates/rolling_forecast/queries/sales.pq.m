// rotm-stamp: contract sha256:498ce1b97012145d6805e01f37a9844f5471a8d1133942d2ea115740d72bfcf9; body sha256:0412fdee61dc7444e6ffc7fe9cc9640879f57c68e73b528baabcdc16a54b0da7
let
    // rule-of-thumb-model: Power Query for raw table 'sales' (contracts/sales.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\sales.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"line_id", "sale_date", "product", "revenue"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"line_id", "sale_date", "product", "revenue"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"line_id", type text}, {"sale_date", type date}, {"product", type text}, {"revenue", type number}}, "en-US")
in
    Typed
