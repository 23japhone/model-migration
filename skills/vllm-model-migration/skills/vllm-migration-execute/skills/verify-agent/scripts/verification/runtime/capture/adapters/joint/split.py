"""将算子实参与冻结的 worker 区间逐项对照。"""


def lengths(values):
    return values.tolist() if hasattr(values, 'tolist') else list(values)


def extent(values, i):
    end = values[i]
    return end - (values[i - 1] if i else 0)


def apply(items, operation):
    for item in items:
        try:
            operation(item)
        except Exception as error:
            item.errors.append(str(error))


class Member:
    def __init__(self, row, step, group, store, record):
        self.row = row
        self.step = step
        self.group = group
        self.store = store
        self.errors = record['errors']
        self.record = record
        self.begin, self.end = row['token_range']
        self.index = row['batch_row']

    def segment(self, tensor):
        return tensor[self.begin : self.end]
