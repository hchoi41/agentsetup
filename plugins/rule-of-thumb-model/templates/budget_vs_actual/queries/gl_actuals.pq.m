// rotm-stamp: contract sha256:876d191e002f95cbea3a42e438822a7c4f8dd453dcdc9b1781bcbf6eff2ed650; body sha256:3d485360196690da995df0ebe82be0bc783638f58fbae0155a15a4eb6dd30d49
let
    // rule-of-thumb-model: Power Query for raw table 'gl_actuals' (contracts/gl_actuals.contract.json).
    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.
    Source = Csv.Document(File.Contents("<model folder>\raw\gl_actuals.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"entry_id", "posting_date", "cost_centre", "account", "amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"entry_id", "posting_date", "cost_centre", "account", "amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"entry_id", type text}, {"posting_date", type date}, {"cost_centre", type text}, {"account", type text}, {"amount", type number}}, "en-US")
in
    Typed
