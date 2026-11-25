// rotm-stamp: contract sha256:20d21eb3ac6f06197c90dff9a243f0e5211b11beec73b5761ce302c914c20b0d; body sha256:84e27641f3686d49eb5566d70d5ece16f71254a55d01d29ce805543cf7dd4c41
let
    // rule-of-thumb-model: Power Query for raw table 'target' (contracts/target.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\target.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"region", "month_end", "target_amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"region", "month_end", "target_amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"region", type text}, {"month_end", type date}, {"target_amount", type number}}, "en-US")
in
    Typed
