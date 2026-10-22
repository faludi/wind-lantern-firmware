# license: https://github.com/sourav-nanda/micropython-url_encode

class url_encode:

    def encode(self,string):

       encoded_string = ""

       for character in string:
           if character.isalpha() or character.isdigit():
               encoded_string += character
           else:
               encoded_string += f"%{ord(character):x}"

       return encoded_string


    def decode(self,url):
        counter = 0 
        while '%' in url and counter < 100:  # A check to break out of the loop after 100 iterations
           index = url.index('%')
           hex_code = url[index+1:index+3]
           char = chr(int(hex_code, 16))
           url = url[:index] + char + url[index+3:]
           counter += 1  
        return url


def form_decode(value):
    """Decode an application/x-www-form-urlencoded value ('+' and UTF-8 percent escapes)."""
    value = value.replace("+", " ")
    out = bytearray()
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "%":
            out.append(int(value[i + 1:i + 3], 16))
            i += 3
        else:
            out.extend(value[i].encode())
            i += 1
    return out.decode()


 
