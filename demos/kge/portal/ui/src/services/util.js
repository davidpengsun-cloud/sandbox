export function sortArray(key, order = 'asc') {
    return function innerSort(value1, value2) {
        if (!value1.hasOwnProperty(key) || !value2.hasOwnProperty(key)) {
            // property doesn't exist on either object
            return 0;
        }

        const varA = (typeof value1[key] === 'string')
            ? value1[key].toUpperCase() : value1[key];
        const varB = (typeof value2[key] === 'string')
            ? value2[key].toUpperCase() : value2[key];

        let comparison = 0;
        if (varA > varB) {
            comparison = 1;
        } else if (varA < varB) {
            comparison = -1;
        }
        return (
            (order === 'desc') ? (comparison * -1) : comparison
        );
    };
}

export function sortArrayBasedOnDates(key, order) {
    return function innerSort(value1, value2) {
        if (order === 'desc') {
            return Date.parse(new Date(value1[key])) - Date.parse(new Date(value2[key]))
        } else if (order === 'asc') {
            return Date.parse(new Date(value2[key])) - Date.parse(new Date(value1[key]))
        }
    }
}