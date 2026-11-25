// rotm-stamp: contract sha256:646c2d36ebd3520e4d8344a7a1ad0a7a611b66223193a9dee3760118216abb8f; body sha256:71dae9bdf1b92d98aff6a435c72c55cbfb39cac893ba601f39c387da15123593
let
    // rule-of-thumb-model: Power Query for raw table 'open_items' (contracts/open_items.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\open_items.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"invoice_id", "customer", "invoice_date", "due_date", "open_amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"invoice_id", "customer", "invoice_date", "due_date", "open_amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"invoice_id", type text}, {"customer", type text}, {"invoice_date", type date}, {"due_date", type date}, {"open_amount", type number}}, "en-US")
in
    Typed
